import argparse
import numpy as np 
import scipy as sp
import pandas as pd
from pandas.plotting import register_matplotlib_converters
register_matplotlib_converters()
from scipy import stats, optimize, interpolate
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os
import time
from datetime import datetime

"""
The model learns its parameters from C and D. see docstring of train()
These parameters can be used for R0 estimation and for making other 
predictions.

I generated a dummy dataset that was in paper section 3.3. I set 
N=s0=500 instead of 5364500 for speed. see __name__ == __main__:
"""


def metropolis_hastings(x, data, fn, proposal, conditions_fn, burn_in=1):
    """
    get 1 sample from a distribution p(x) ~ k*fn(x) given proposal
    distribution proposal(x) with metropolis hastings algorithm

        * the new sample has to satisfy the conditions in conditions_fn
        * data is a list of additional distribution, variables etc that are
          required to compute the functions
        * assumes proposal distribution is symmetric, ie: q(x'|x) = q(x|x')
        * fn returns log prob. for numeric stability

    returns: one sample from p(x) and corresponding data
    """
    old_log_prob = fn(x, data)
    while burn_in:
        burn_in -= 1
        x_new, data_new = proposal(x, data, conditions_fn)
        accept_log_prob = min(0, fn(x_new, data_new) - fn(x, data))
        p = np.log(np.random.uniform(0, 1))
        if p <= accept_log_prob:
            x, data = x_new, data_new #, fn(x_new, data_new), fn(x, data)
        else:
            pass
    return x, data, fn(x, data), old_log_prob



def train(N, D_wild, inits, params, priors, rand_walk_stds, t_ctrl, tau, n_iter, n_burn_in, bounds, save_freq):
    """
    C = the number of cases by date of symptom onset
    D = the number of cases who are removed (dead or recovered)
    N = total population
    inits is a list of:
        s0 = S(0): number of suspected individuals at t=0
        e0 = E(0): number of exposed individuals at t=0
        i0 = I(0): number of infected individuals at t=0
             (this is called a in paper)
    priors = list of gamma prior parameters for four model parameters.
             The parameters are:
                beta: uncontrolled transmission rate
                q: parameter for time dependent controlled transmission rate
                g: 1/mean incubation period
                gamma: 1/mean infectious period

    rand_walk_stds = proposal dist for MCMC parameter update is a normal distribution
                     with previous sample as mean and standard deviation from
                     rand_walk_stds. There is one std for each of the four model
                     parameters. 

                     TODO: For faster convergence, the stds should be tuned such that 
                     mean acceptance probability is in between 0.23 and 0.5.

    t_end = end of observation
    t_ctrl = day of intervention
    tau = end of epidemics in days > t_end
    
    n_iter = number of iterations
    n_burn_in = burn in period for MCMC
    
    m = total number of infected individuals throughout the course of the disease = sum(B)
    

    returns: the distribution of B and params. They can be used later to calculate R0 and extrapolate

    """
    t_end = len(N)
    assert t_end < tau
    assert t_ctrl < tau
    assert len(N) == len(D_wild)
    assert n_burn_in < n_iter

    # initialize model parameters
    e0, i_mild0, i_wild0 = inits
    beta, q, delta, rho, gamma_mild, gamma_wild, k = params
    print("Initializating Variables...")
    S, E, I_mild, I_wild, B, C, D_mild, P, t_rate, N = initialize(inits, params, N, D_wild, t_ctrl)
    epsilon = 1e-16
    print("Initialization Complete.")
    check_rep_inv(S, E, I_mild, I_wild, B, C, D_mild, D_wild, P, N, inits, params, t_ctrl, t_end)

    # initialize B and params
    print(f"n_burn_in:{n_burn_in}")
    # to show final statistics about params
    saved_params = []
    saved_R0ts = []

    start_time = time.time()
    t0 = start_time
    t1 = start_time
    for i in range(n_iter):
        # MCMC update for B, S, E
        beta, q, delta, rho, gamma_mild, gamma_wild, k = params

        B, S, E, log_prob_new, log_prob_old = sample_B(B, [S, E, I_mild, I_wild, C, P, N], inits, params, t_ctrl, epsilon)
        check_rep_inv(S, E, I_mild, I_wild, B, C, D_mild, D_wild, P, N, inits, params, t_ctrl, t_end)
        
        C, E, I_mild, I_wild, P, _, _ = sample_C(C, [E, I_mild, I_wild, D_mild, D_wild, B, N, P], inits, params, t_ctrl, epsilon)
        check_rep_inv(S, E, I_mild, I_wild, B, C, D_mild, D_wild, P, N, inits, params, t_ctrl, t_end)
        
        D_mild, I_mild, P, _, _ = sample_D_mild(D_mild, [I_mild, I_wild, C, N], inits, params, t_ctrl, epsilon)
        check_rep_inv(S, E, I_mild, I_wild, B, C, D_mild, D_wild, P, N, inits, params, t_ctrl, t_end)
        
        # MCMC update for params and P
        # I is fixed by C and D and doesn't need to be updated
        params, S, E, I_mild, I_wild, P, N, R0t, log_prob_new, log_prob_old = sample_params(params, 
                                                                    [S, E, I_mild, I_wild, B, C, D_mild, D_wild, P, N], 
                                                                    inits, priors, rand_walk_stds, t_ctrl, epsilon, bounds
                                                                   )
        check_rep_inv(S, E, I_mild, I_wild, B, C, D_mild, D_wild, P, N, inits, params, t_ctrl, t_end)
        
        if i >= n_burn_in and i % save_freq == 0:
            saved_params.append(params)
            saved_R0ts.append(R0t)

        if i % 20 == 0:
            beta, q, delta, rho, gamma_mild, gamma_wild, k = np.round(params, 5)
            params_dict = {'beta': beta, 'q': q, 'delta': delta, 'rho': rho,
                           'gamma_mild':gamma_mild, 'gamma_wild':gamma_wild, 'k': k,
                           'log_prob_new':np.round(log_prob_new, 5), 'diff':np.round(log_prob_new-log_prob_old, 5) 
                           }
            print(f"iter {i}:\n{params_dict}")
            t1 = time.time()
            print("iter %d: Time %.2f | Runtime: %.2f" % (i, t1 - start_time, t1 - t0))
            print(f"B:\n{B}")
            print(f"C:\n{C}")
            print(f"D_mild:\n{D_mild}")
            print(f"D_wild:\n{D_wild}")
            t0 = t1

    R0s = [(sum(D_mild)+sum(D_wild)) * p[0] / (sum(D_mild)*p[3]+sum(D_wild)*p[4]) for p in saved_params]

    # 80% CI
    CI_FACTOR = 1.96
    R0_low = np.mean(R0s) - CI_FACTOR * np.std(R0s)
    R0_high = np.mean(R0s) + CI_FACTOR * np.std(R0s)

    R0ts_mean = np.mean(saved_R0ts, axis=0)
    R0ts_std = np.std(saved_R0ts, axis=0)
    
    return C, np.mean(saved_params, axis=0), np.std(saved_params, axis=0), (R0_low, R0_high), (R0ts_mean, R0ts_std)


def round_int(x):
    return np.floor(x+0.5).astype(int)

def check_rep_inv(S, E, I_mild, I_wild, B, C, D_mild, D_wild, P, N, inits, params, t_ctrl, t_end):
    """
    check rep invariant
    """
    e0, i_mild0, i_wild0 = inits
    beta, q, delta, rho, gamma_mild, gamma_wild, k = params
    assert (I_mild >= 0).all()    
    assert (I_wild >= 0).all()  
    assert (S >= 0).all()
    assert (E >= 0).all()
    assert (I_mild + I_wild >= 0).all()
    assert (B >= 0).all()
    assert (C >= 0).all()
    assert (D_mild >= 0).all()
    assert (D_wild >= 0).all()
    # P is a list of binomial parameters
    assert (1 >= P).all() and (P >= 0).all()
    assert (S==compute_S(e0, i_mild0, i_wild0, B, N)).all()
    assert (E==compute_E(e0, B, C)).all()
    assert (I_mild==compute_I(i_mild0, round_int(C*delta), D_mild)).all()
    assert (I_wild==compute_I(i_wild0, C-round_int(C*delta), D_wild)).all()
    assert (P == compute_P(transmission_rate(beta, q, t_ctrl, t_end), I_mild, I_wild, N)).all()

def sample_x(x, data, conditions_fn, data_fn):
    """
    x:  a sample from p(B|.)
    data = [P, I, S, E], and P doesn't depend on x
    
    sampling for B works as follows (according to paper)
        1. randomly select an index t' such that B[t'] > 0
        2. set B[t'] -= 1
        3. randomly select an index t^
        4. set B[t^] += 1
        5. compute S and E for this new B
        6. Verify that E >= 0 (S >= 0 obviously since sum(B) is constant)
        7. Verify I+E>0
    The authors suggested to select N*10% indices instead of 1 for faster convergence
    """
    n_tries = 0
    x_new = np.copy(x)
    while n_tries < 100:
        n_tries += 1
        t_new = np.random.choice(np.nonzero(x_new >= 1)[0], min(15, len(np.nonzero(x_new)[0])), replace=False)
        t_tilde = np.random.choice(range(len(x)), len(t_new), replace=False)
        # t_new += 1
        assert(x_new[t_new] >= 1).all()
        one_off = np.random.binomial(1, 0.5)
        if one_off:
            change_add = 1
            change_subs = 1
        else:
            # 80 and 79 makes the dist symmetric
            # 79 is the solution 'y' of
            # (n+n/y)-(n+n/y)/80) = n
            change_add = np.copy(x_new[t_tilde]//79)
            change_subs = np.copy(x_new[t_new]//80)
        
        x_new[t_new] -= change_subs
        x_new[t_tilde] += change_add
        
        data_new = data_fn(x_new)

        if conditions_fn(x_new, data_new):
            return x_new, data_new
        else:
            # revert back the changes
            x_new[t_new] += change_subs
            x_new[t_tilde] -= change_add

    # print("no sample found")
    return x, data

def sample_B(B, variables, inits, params, t_ctrl, epsilon):
    """
    get a sample from p(B|C, D, params) using metropolis hastings
    """

    def fn(x, data):
        S, E = data
        # add epsilon to prevent log 0.
        return np.sum(np.log(sp.stats.binom(S, P).pmf(x)+epsilon))


    def proposal(x, data, conditions_fn):
        def data_fn(x):
            S_new = compute_S(e0, i_mild0, i_wild0, x, N)
            E_new = compute_E(e0, x, C)
            return S_new, E_new
        return sample_x(x, data, conditions_fn, data_fn)


    def conditions_fn(x, data):
        S, E = data
        # print((S>=0).all())
        # print((E>=0).all())
        return  (S>=0).all() and (E>=0).all() #and (E+I_mild+I_wild>0).all()


    t_end = len(B)
    e0, i_mild0, i_wild0 = inits
    beta, q, delta, rho, gamma_mild, gamma_wild, k = params
    S, E, I_mild, I_wild, C, P, N = variables
    
    data = [S, E]
    B, data, log_prob_new, log_prob_old = metropolis_hastings(B, data, fn, proposal, conditions_fn, burn_in=1)
    S, E = data

    return B, S, E, log_prob_new, log_prob_old

def sample_C(C, variables, inits, params, t_ctrl, epsilon):
    """
    get a sample from p(B|C, D, params) using metropolis hastings
    """

    def fn(x, data):
        E, I_mild, I_wild, P = data
        # add epsilon to prevent log 0.
        pC = 1-np.exp(-rho)
        assert 0 <= pC <= 1
        return np.sum(np.log(sp.stats.binom(E, pC).pmf(x)+epsilon))


    def proposal(x, data, conditions_fn):
        def data_fn(x):
            E_new = compute_E(e0, B, x)
            I_mild_new = compute_I(i_mild0, round_int(delta*x), D_mild)
            I_wild_new = compute_I(i_wild0, x - round_int(delta*x), D_wild)
            P_new = compute_P(transmission_rate(beta, q, t_ctrl, t_end), I_mild_new, I_wild_new, N)
            return E_new, I_mild_new, I_wild_new, P_new
        return sample_x(x, data, conditions_fn, data_fn)


    def conditions_fn(x, data):
        E, I_mild, I_wild, P = data
        return  (E>=0).all() and (I_mild>=0).all() and (I_wild>=0).all()# and (E+I_mild+I_wild > 0).all()


    t_end = len(C)
    e0, i_mild0, i_wild0 = inits
    beta, q, delta, rho, gamma_mild, gamma_wild, k = params
    E, I_mild, I_wild, D_mild, D_wild, B, N, P = variables
    
    data = [E, I_mild, I_wild, P]
    C, data, log_prob_new, log_prob_old = metropolis_hastings(C, data, fn, proposal, conditions_fn, burn_in=1)
    E, I_mild, I_wild, P = data

    return C, E, I_mild, I_wild, P, log_prob_new, log_prob_old

def sample_D_mild(D_mild, variables, inits, params, t_ctrl, epsilon):
    """
    get a sample from p(B|C, D, params) using metropolis hastings
    """

    def fn(x, data):
        I_mild = data[0]
        # assert (S >= x).all()
        # assert (x >= 0).all()
        # add epsilon to prevent log 0.
        pR = 1-np.exp(-gamma_mild)
        assert 0 <= pR <= 1
        assert not np.isnan(pR)
        return np.sum(np.log(sp.stats.binom(I_mild, pR).pmf(x)+epsilon))
        

    def proposal(x, data, conditions_fn):
        I_mild = data[0]
        def data_fn(x):
            I_mild_new = compute_I(i_mild0, round_int(C*delta), x)
            return [I_mild_new]
        
        return sample_x(x, data, conditions_fn, data_fn)

    def conditions_fn(x, data):
        I_mild = data[0]
        return (I_mild>=0).all()

    t_end = len(D_mild)
    e0, i_mild0, i_wild0 = inits
    beta, q, delta, rho, gamma_mild, gamma_wild, k = params
    I_mild, I_wild, C, N = variables
    data = [I_mild]
    D_mild, data, log_prob_new, log_prob_old = metropolis_hastings(D_mild, data, fn, proposal, conditions_fn, burn_in=1)
    I_mild = data[0]
    P = compute_P(transmission_rate(beta, q, t_ctrl, t_end), I_mild, I_wild, N)
    return [D_mild] + data + [P, log_prob_new, log_prob_old]

def sample_params(params, variables, inits, priors, rand_walk_stds, t_ctrl, epsilon, bounds):
    """
    update beta, q, g, gamma with independent MCMC sampling
    each of B, C, D is a list of binomial distributions. The prior is a gamma distribution for each parameter 
    
    proposal distribution is univariate gaussian centered at previous value and sigma from rand_walk_stds (there
    are four; one for each param). 

    """
    def fn(x, data):
        """
        here x is equal to one of beta, q, g, gamma. since we compute the same likelihood
        function to update each of the params, it is sufficient to use this generic function
        instead of writing one fn function for each param.

        other_data['which_param'] stores the parameter to update. it is an index of params

        """
        beta, q, delta, rho, gamma_mild, gamma_wild, k = x
        S, E, I_mild, I_wild, P, N = data

        pC = 1 - np.exp(-rho)
        pR_mild = 1 - np.exp(-gamma_mild)
        pR_wild = 1 - np.exp(-gamma_wild)

        # log likelihood
        # add epsilon to avoid log 0.
        logB = np.sum(np.log(sp.stats.binom(S, P).pmf(B) + epsilon))
        logC = np.sum(np.log(sp.stats.binom(E, pC).pmf(C) + epsilon))
        logD_mild = np.sum(np.log(sp.stats.binom(I_mild, pR_mild).pmf(D_mild) + epsilon))
        logD_wild = np.sum(np.log(sp.stats.binom(I_wild, pR_wild).pmf(D_wild) + epsilon))

        assert not np.isnan(logB)
        assert not np.isnan(logC)
        assert not np.isnan(logD_mild)
        assert not np.isnan(logD_wild)

        # log prior
        log_prior = 0
        for i in range(len(priors)):
            a, b = priors[i]
            log_prior += np.log(sp.stats.gamma(a, b).pdf(x[i])+epsilon)
        assert not np.isnan(log_prior)        
        return logB + logC + logD_mild + logD_wild + log_prior

    def proposal(x, data, conditions_fn):
        """
        see docstring for previous function
        """
        S, E, I_mild, I_wild, P, N = data
        n_tries = 0
        while n_tries < 100:
            n_tries += 1
            
            x_new = np.random.normal(x, rand_walk_stds)
            beta, q, delta, rho, gamma_mild, gamma_wild, k = x_new
            
            factor_indices = np.array(range(len(N)))
            # factor_old = 1/old_k-old_kctrl*np.log(1+np.exp(factor_indices-t_ctrl))
            # factor_new = 1/k-kctrl*np.log(1+np.exp(factor_indices-t_ctrl))
    
            N_new = round_int(N*old_k / k)
            N_new[N_new<1] = 1
            S_new = compute_S(e0, i_mild0, i_wild0, B, N_new)
            E_new = compute_E(e0, B, C)
            I_mild_new =compute_I(i_mild0, round_int(C*delta), D_mild)
            I_wild_new =compute_I(i_wild0, C-round_int(C*delta), D_wild)            
            P_new = compute_P(transmission_rate(beta, q, t_ctrl, t_end), I_mild_new, I_wild_new, N_new)
            data_new = [S_new, E_new, I_mild_new, I_wild_new, P_new, N_new]

            if conditions_fn(x_new, data_new):
                # print(x_new-x, fn(x_new, data_new)-fn(x, data))
                return x_new, data_new
        print("sample not found")
        return x, data
    
    def conditions_fn(x, data):
        """
        all parameters should be non-negative
        """
        beta, q, delta, rho, gamma_mild, gamma_wild, k = x
        S, E, I_mild, I_wild, P, N = data
        
        # if not 1/k-kctrl*np.log(1+np.exp(len(N)-t_ctrl)) > 0:
        #     return False

        if not (x > 0).all():
            return False
        
        if not (S >= 0).all() or not (E >= 0).all() or not (I_mild >= 0).all() or not (I_wild >= 0).all():
            return False

        for i in range(len(bounds)):
            a, b = bounds[i]
            param = x[i]
            if x[i] < a or x[i] > b:
                print("failed here")
                return False
        return True

    e0, i_mild0, i_wild0 = inits
    S, E, I_mild, I_wild, B, C, D_mild, D_wild, P, N = variables
    t_end = len(N)
    beta, q, delta, rho, gamma_mild, gamma_wild, k = params
    old_k = k
    data = [S, E, I_mild, I_wild, P, N]

    params_new, data, log_prob_new, log_prob_old = metropolis_hastings(np.array(params), data, fn, proposal, conditions_fn, burn_in=1)
    beta, q, delta, rho, gamma_mild, gamma_wild, k = params_new
    t_rate = transmission_rate(beta, q, t_ctrl, t_end)
    # R0t = (sum(D_mild)+sum(D_wild))*t_rate /((sum(D_mild)*gamma_mild+sum(D_wild)*gamma_wild)) * S/N
    R0t = t_rate /(delta*gamma_mild+(1-delta)*gamma_wild) * S/N
    
    S, E, I_mild, I_wild, P, N = data
    
    return params_new.tolist(), S, E, I_mild, I_wild, P, N, R0t, log_prob_new, log_prob_old


def compute_S(e0, i_mild0, i_wild0, B, N):
    """
    S(0) = s0
    S(t+1) = S(t) - B(t) + N(t+1)-N(t) for t >= 0

    can be simplified to S(t+1) = s0 - sum(B[:t])
    """
    return N[0] - np.concatenate(([0], np.cumsum(B)[:-1])) + N - N[0]

def compute_E(e0, B, C):
    """
    E(0) = e0
    E(t+1) = E(t) + B(t) - C(t) for t >= 0

    can be simplified to E(t+1) = e0+sum(B[:t]-C[:t])
    """
    return e0 + np.concatenate(([0], np.cumsum(B - C)[:-1]))


def compute_I(i0, C, D):
    """
    computes either I_mild or I_wild depending on the inputs
    I(0) = i0
    I(t+1) = I(t) + C(t) - D(t) for t >= 0

    can be simplified to I(t+1) = i0+sum(C[:t]-D[:t])
    """
    return i0 + np.concatenate(([0], np.cumsum(C - D)[:-1]))


def transmission_rate(beta, q, t_ctrl, t_end):
    """
    rate of transmission on day t, ie. the number of
    newly infected individuals on day t.
    
    This is defined to be beta prior to t_ctrl and beta*exp(-q(t-t_ctrl)) after t_ctrl

    Note: this is different from R0
    """
    trans_rate = np.ones((t_end, )) * beta
    if t_ctrl < t_end:
        ctrl_indices = np.array(range(t_ctrl, t_end))
        trans_rate[ctrl_indices] = beta * np.exp(-q * (ctrl_indices - t_ctrl))

    assert trans_rate.all() >= 0
    return trans_rate

def compute_P(trans_rate, I_mild, I_wild, N):
    """
    P[t] = 1 - exp(-BETA[t] * I[t] / N)
    here BETA[t] = time dependent transmission rate
    """
    P = 1 - np.exp(-trans_rate * (I_mild+I_wild) / N)
    return P


def compute_rand_walk_cov(t, t_skip, C0, C_t, mean_t, mean_tm1, x_t, epsilon):
    assert t_skip > 2
    if t < t_skip:
        return C0
    else:
        return (t-1)/t * C_t + 2.4**2/len(C0) * (t*mean_tm1@mean_tm1.T-(t+1)*mean_t@mean_t.T + x_t@x_t.T+epsilon*np.identity(len(C0)))


def read_dataset(filepath, start, end, n, k):
    """
    start, end datetime object in YYYY-MM-DD format
    """
    df = pd.read_csv(filepath)
    df['date'] = pd.to_datetime(df.iloc[:, 0])
    
    if (start-df['date'].iloc[0]).days <= n:
        raise ValueError("start value smaller than rolling mean window is not allowed")
    start_offset =  (start - df['date'].iloc[0]).days
    end_offset =  (df['date'].iloc[-1] - end).days
    
    print(start_offset, end_offset)

    N = df.num_confirmed.rolling(5).mean()[start_offset:-end_offset].to_numpy()
    D_wild = df.num_confirmed_that_day.rolling(5).mean()[start_offset:-end_offset].to_numpy()
    
    D_wild[D_wild <= 0] = 0
    N = round_int(N/k)
    N[N < 1] = 1
    return N, round_int(D_wild), df['date'][start_offset: -end_offset]


def initialize(inits, params, N, D_wild, t_ctrl, attempt=100):
    beta, q, delta, rho, gamma_mild, gamma_wild, k = params
    e0, i_mild0, i_wild0 = inits
    P, B, C, D_mild, N_new = [], [], [], [], []
    S, E, I_mild, I_wild = [N[0]], [e0], [i_mild0], [i_wild0] 
    t_rate = transmission_rate(beta, q, t_ctrl, len(N))
    
    for t in range(len(N)-1):
        s, e, i_mild, i_wild = S[t], E[t], I_mild[t], I_wild[t]
        # print(i_mild, i_wild)
        p = 1-np.exp(-t_rate[t]*(i_mild+i_wild)/N[t])
        assert 0 <= p <=1
        
        d_wild = int(D_wild[t])
        b = round_int(s*p)
        c = round_int(e*(1-np.exp(-rho)))
        d_mild = round_int(i_mild*(1-np.exp(-gamma_mild)))

        c_mild = round_int(c*delta)
        c_wild = c-c_mild
        
        # b <= s cause binom dist, so s >= 0
        s = s - b + N[t+1] - N[t]
        e = e + b - c
        i_mild = i_mild + c_mild - d_mild
        i_wild = i_wild + c_wild - d_wild
        print(f"t: {t}, S[t]{s} E[t]:{e} I_mild[t]: {i_mild} I_wild[t]: {i_wild}")
        assert i_wild >= 0
        S.append(s)
        E.append(e)
        I_mild.append(i_mild)
        I_wild.append(i_wild)
        
        B.append(b)
        C.append(c)
        D_mild.append(d_mild)
        P.append(p)

    # last step
    p = 1-np.exp(-t_rate[-1]*(I_mild[-1]+I_wild[-1])/N[-1])
    b = np.random.binomial(S[-1], p)
    d_mild = int(I_mild[-1]*(1-np.exp(-gamma_mild)))
    c = round_int(E[-1]*(1-np.exp(-rho)))

    B.append(b)
    C.append(c)
    P.append(p)
    D_mild.append(d_mild)

    return [np.array(S), np.array(E), np.array(I_mild), np.array(I_wild), 
            np.array(B), np.array(C), np.array(D_mild), np.array(P), t_rate, np.array(N)]

    # params = [2, 0.05, 0.6, 0.15, 0.33, 0.2] # korea
    
    # korea
    # params = [2, 0.05, 0.6, 0.15, 0.33, 0.2]
    # n = 3
    # offset, last_offset = 30, 1
    # lockdown = 37 # 
    # rand_walk_stds = [0.01, 0.002, 0.002, 0.002, 0.002, 0.002] # [0.01, 0.001, 0.001, 0.001, 0.001, 0.001]

    # italy
    # params = [0.5, 0.001, 0.8, 0.18, 0.33, 0.1]
    # n = 5
    # offset, last_offset = 30, 1
    # lockdown = 47

    # wuhan
    # params = [0.5, 0.001, 0.8, 0.18, 0.33, 0.1]
    # n = 5
    # offset, last_offset = 2, 12
    # lockdown = 3

    # new york
    # params = [0.6, 0.001, 0.8, 0.18, 0.33, 0.18]
    # n = 5
    # offset, last_offset = 40, 1
    # lockdown = 5

    # germany
    # params = [0.7, 0.001, 0.6, 0.18, 0.33, 0.22]
    # n = 5
    # offset, last_offset = 33, 1
    # lockdown = 58

    # california
    # params = [0.7, 0.001, 0.7, 0.18, 0.33, 0.22]
    # n = 5
    # offset, last_offset = 43, 1
    # lockdown = 57

    # turkey
    # filename = os.path.join(dirname, '../datasets/china_mar_30.csv')
    # out_filename = os.path.join(dirname, '../output_china_start_jan23_lockdown_none.txt')
    # params = [0.7, 0.001, 0.8, 0.18, 0.33, 0.22]
    # n = 5
    # offset, last_offset = 49, 1
    # lockdown = 66

    # china
    # filename = os.path.join(dirname, '../datasets/china_mar_30.csv')
    # out_filename = os.path.join(dirname, '../output_china_start_jan23_lockdown_jan28.txt')
    # params = [0.8, 0.001, 0.8, 0.18, 0.33, 0.1]
    # n = 5
    # offset, last_offset = 1, 1
    # lockdown = 6
    # rand_walk_stds = [0.008, 0.0005, 0.0005, 0.001, 0.001, 0.0007] # no need to change

    # us
    # filename = os.path.join(dirname, '../datasets/us_mar_30.csv')
    # out_filename = os.path.join(dirname, '../output_us_start_feb25_lockdown_none.txt')
    # params = [0.8, 0.001, 0.8, 0.18, 0.33, 0.1]
    # n = 5
    # offset, last_offset = 34, 1
    # lockdown = 66
    # rand_walk_stds = [0.008, 0.001, 0.001, 0.001, 0.001, 0.001] # no need to change


def plot_R0ts(R0ts_mean, R0ts_std, CI_FACTOR, out_filename):
    plt.style.use('seaborn-darkgrid')
    line, = plt.plot(dates[1:], R0ts_mean[1:], marker='o', linestyle='solid', linewidth=2, markersize=5, label='Effective R0(t)')
    plt.fill_between(dates[1:], R0ts_mean[1:]-CI_FACTOR*R0ts_std[1:], R0ts_mean[1:]+CI_FACTOR*R0ts_std[1:],facecolor='C0',alpha=0.2)
    point = plt.stem([lockdown], [R0ts_mean[t_ctrl]], linefmt='C1-', markerfmt='C1o', label='lockdown', use_line_collection=True)
    
    plt.xticks(fontsize=12)
    plt.yticks(fontsize=12)
    plt.legend(handles=[line, point], fontsize=12)
    plt.title(args.infile, fontsize=16)
    
    ax = plt.gca()
    ax.tick_params(axis='both', which='major', width=1.4, length=7, direction='out')
    ax.tick_params(axis='both', which='minor', width=1.1, length=4, direction='in')
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=7))
    ax.xaxis.set_minor_locator(mdates.DayLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
    
    plt.gcf().set_size_inches(13, 8)
    plt.savefig(out_filename[:-4]+".png", dpi=200)
    plt.show()

def parse_arguments():
    default_in_dir = '../datasets'
    default_out_dir = 'output'
    default_in_filename = 'korea_april_16.csv'
    if not os.path.exists(default_out_dir):
        os.makedirs(default_out_dir)

    parser = argparse.ArgumentParser(description='Learn an SEIR model for the COVID-19 infected data.')
    parser.add_argument('--infile', type=str, help='Path for the location of the input file',
                        default=default_in_filename, nargs='?')
    parser.add_argument('--indir', type=str, help='Directory for the location of the input file',
                        default=default_in_dir, nargs='?')
    parser.add_argument('--outdir', type=str, help='Directory for the location of the output file',
                        default=default_out_dir, nargs='?')
    parser.add_argument('--inits', type=str, default="500, 100, 100", nargs='?', help="inits e0, imild0, iwild0")
    parser.add_argument('--params', type=str, default="1.9, 0.01, 0.5, 0.5, 0.12, 0.2, 0.2", nargs='?', 
                        help="inits for beta, q, delta, rho, gamma_mild, gamma_wild, k")
    parser.add_argument('--n', type=int, default=3, nargs='?', help="number of entries to take rolling mean over")
    parser.add_argument('--start', type=str, default='2020-02-19', nargs='?', 
                        help="first day in the model in YYYY-MM-DD format")
    parser.add_argument('--end', type=str, default='2020-04-15', nargs='?', help="last day in the model in YYYY-MM-DD format")
    parser.add_argument('--lockdown', type=str, default='2020-02-28', nargs='?', 
                        help="the day on which national lockdown was imposed in YYYY-MM-DD format")
    parser.add_argument('--n_iter', type=int, default=20, nargs='?', help="number of iterations")
    parser.add_argument('--n_burn_in', type=int, default=10, nargs='?', help="burn in period for MCMC")
    parser.add_argument('--save_freq', type=int, default=5, nargs='?', help="how often to save samples after burn in")
    parser.add_argument('--rand_walk_stds', type=str, default="0.001, 0.001, 0.001, 0.001, 0.001, 0.001, 0.001", nargs='?', 
                       help="stds for gaussian random walk in MCMC (one for each param)")

    # beta, q, delta, rho, gamma_mild, gamma_wild, k
    args = parser.parse_args()
    return args


if __name__ == '__main__':
    dirname = os.path.dirname(__file__)
    args = parse_arguments()
    bounds=[(0, 6), (0, np.inf), (0.08, 0.92), (0., 0.9), (0., 0.9), (0., 0.9), (0, 1)]
    params = [float(param) for param in args.params.split(',')] # italy
    rand_walk_stds = [float(std) for std in args.rand_walk_stds.split(',')]
    assert len(params) == 7 and len(rand_walk_stds) == 7, "Need all parameters and their random walk stds"
    n = args.n
    start, end = datetime.strptime(args.start, '%Y-%m-%d'), datetime.strptime(args.end, '%Y-%m-%d')
    lockdown = datetime.strptime(args.lockdown, '%Y-%m-%d')
    in_filename = os.path.join(dirname, args.indir + '/'+ args.infile)
    out_filename = args.outdir+f"/seir_{args.infile}_start{start.strftime('%m-%d')}_lockdown{lockdown.strftime('%m-%d')}_end{end.strftime('%m-%d')}.txt"

    t_ctrl = (lockdown-start).days          # day on which control measurements were introduced
    assert t_ctrl >= 0

    N, D_wild, dates = read_dataset(in_filename, start, end, n, params[6]) # k = smoothing factor
    # Imild(0), Iwild(0)
    inits = [int(init) for init in args.inits.split(',')]
    priors = [(2, 10)]*len(params) # no need to change
    
    tau = 1000           # no need to change
    n_iter = args.n_iter      # no need to change
    n_burn_in = args.n_burn_in    # no need to change
    save_freq = args.save_freq
    
    
    params_mean, params_std, R0_conf, R0ts = train(N, D_wild, inits, params, priors, 
                                                        rand_walk_stds, t_ctrl, tau, n_iter, n_burn_in, bounds, save_freq
                                                       )[1:]
    print(f"\nFINAL RESULTS\n\ninput file: {in_filename}")
    print(f"ouput file: {out_filename}")
    print(f"param inits: {params}")
    print(f"start:{start.strftime('%Y-%m-%d')}, lockdown:{lockdown.strftime('%Y-%m-%d')}")
    print(f"parameters (beta, q, delta, rho, gamma_mild, gamma_wild, k): mean: {params_mean}, std={params_std}\n\n"
          +f"R0 95% confidence interval: {R0_conf}\n\n"
          +f"R0[t] mean and std: {R0ts}"
        )
    
    with open(out_filename, 'w') as out:
        out.write("SEIR MODEL FOR R0t PREDICTION\n---   ---   ---   ---   ---\n"
                 +f"dataset name: {in_filename}\n"
                 +f"output filename: {out_filename}\n\n"
                 +f"inits (imild0, iwild0): {inits}, rand_walk_stds:{rand_walk_stds}\n"
                 +f"lockdown:{lockdown.strftime('%Y-%m-%d')}, t_end:{len(N)}, n_iter:{n_iter}, n_burn_in:{n_burn_in}, save_freq:{save_freq}\n"
                 +f"start:{start.strftime('%Y-%m-%d')}, end:{end.strftime('%Y-%m-%d')}, n:{n}\n"
                 +f"bounds:{bounds}\n"
                 +f"param inits:{params}\n"
                 +f"parameters (beta, q, delta, rho, gamma_mild, gamma_wild, k): mean: {params_mean}, std={params_std}\n\n"
                 +f"R0 95% confidence interval: {R0_conf}\n\n"
                 +f"R0[t] mean and std: {R0ts}\n"
                 )
    out.close()

    R0ts_mean, R0ts_std = R0ts
    CI_FACTOR = 1.96

    plot_R0ts(R0ts_mean, R0ts_std, CI_FACTOR, out_filename)
