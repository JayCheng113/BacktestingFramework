"""Numba JIT-compiled fill kernels and full simulation loop.

If numba is not installed, the module exposes plain Python fallbacks.
"""
from __future__ import annotations

import numpy as np

try:
    import numba as nb
    _HAS_NUMBA = True
except ImportError:
    _HAS_NUMBA = False

if _HAS_NUMBA:
    @nb.jit(nopython=True, cache=True)
    def jit_fill_buy(
        price: float,
        amount: float,
        comm_rate: float,
        min_comm: float,
        slip_rate: float,
    ) -> tuple[float, float, float, float]:
        """JIT buy fill → (shares, fill_price, commission, net_amount)."""
        if amount <= 0.0 or price <= 0.0:
            return 0.0, price, 0.0, 0.0
        fill_price = price * (1.0 + slip_rate)
        comm = amount * comm_rate
        if comm < min_comm:
            comm = min_comm
        if comm >= amount:
            return 0.0, fill_price, 0.0, 0.0
        shares = (amount - comm) / fill_price
        return shares, fill_price, comm, -amount

    @nb.jit(nopython=True, cache=True)
    def jit_fill_sell(
        price: float,
        shares: float,
        sell_comm_rate: float,
        min_comm: float,
        slip_rate: float,
    ) -> tuple[float, float, float, float]:
        """JIT sell fill → (shares, fill_price, commission, net_amount)."""
        if shares <= 0.0 or price <= 0.0:
            return 0.0, price, 0.0, 0.0
        fill_price = price * (1.0 - slip_rate)
        if fill_price <= 0.0:
            fill_price = 0.0
        value = shares * fill_price
        comm = value * sell_comm_rate
        if comm < min_comm:
            comm = min_comm
        if comm > value:
            comm = value
        return shares, fill_price, comm, value - comm

else:
    def jit_fill_buy(
        price: float,
        amount: float,
        comm_rate: float,
        min_comm: float,
        slip_rate: float,
    ) -> tuple[float, float, float, float]:
        if amount <= 0.0 or price <= 0.0:
            return 0.0, price, 0.0, 0.0
        fill_price = price * (1.0 + slip_rate)
        comm = max(amount * comm_rate, min_comm)
        if comm >= amount:
            return 0.0, fill_price, 0.0, 0.0
        shares = (amount - comm) / fill_price
        return shares, fill_price, comm, -amount

    def jit_fill_sell(
        price: float,
        shares: float,
        sell_comm_rate: float,
        min_comm: float,
        slip_rate: float,
    ) -> tuple[float, float, float, float]:
        if shares <= 0.0 or price <= 0.0:
            return 0.0, price, 0.0, 0.0
        fill_price = price * (1.0 - slip_rate)
        if fill_price <= 0.0:
            fill_price = 0.0
        value = shares * fill_price
        comm = max(value * sell_comm_rate, min_comm)
        if comm > value:
            comm = value
        return shares, fill_price, comm, value - comm


# --------------- Full simulation loop (JIT) ---------------

_MAX_TRADES = 100000

if _HAS_NUMBA:
    @nb.jit(nopython=True, cache=True)
    def jit_simulate_loop(
        prices, open_prices, weights, capital,
        comm_rate, sell_comm_rate, min_comm, slip_rate,
    ):
        n = len(prices)
        equity_arr = np.zeros(n)
        daily_ret = np.zeros(n)
        t_entry = np.zeros(_MAX_TRADES, dtype=np.int64)
        t_exit = np.zeros(_MAX_TRADES, dtype=np.int64)
        t_eprice = np.zeros(_MAX_TRADES)
        t_xprice = np.zeros(_MAX_TRADES)
        t_pnl = np.zeros(_MAX_TRADES)
        t_comm = np.zeros(_MAX_TRADES)
        t_weight = np.zeros(_MAX_TRADES)
        tc = 0

        equity_arr[0] = capital
        cash = capital
        shares = 0.0
        prev_weight = 0.0
        entry_bar = np.int64(-1)
        entry_price = 0.0
        entry_comm = 0.0

        for i in range(1, n):
            p_i = prices[i]
            op_i = open_prices[i]
            if p_i != p_i or op_i != op_i:  # NaN check without np.isnan
                equity_arr[i] = equity_arr[i - 1]
                continue

            tw = weights[i]
            diff = tw - prev_weight
            if diff > 1e-3 or diff < -1e-3:
                eq = cash + shares * op_i
                tv = eq * tw
                cv = shares * op_i
                filled = False

                if tv < cv and shares > 0.0:
                    ss = shares if tw == 0.0 else (cv - tv) / op_i
                    fp = op_i * (1.0 - slip_rate)
                    if fp < 0.0:
                        fp = 0.0
                    val = ss * fp
                    cm = val * sell_comm_rate
                    if cm < min_comm:
                        cm = min_comm
                    if cm > val:
                        cm = val
                    if ss > 0.0:
                        filled = True
                        cash += val - cm
                        old = shares
                        shares -= ss
                        if shares < 1e-10:
                            shares = 0.0
                        if old > 0.0 and shares < 1e-10 and entry_bar >= 0:
                            pnl_val = (fp - entry_price) * old - cm - entry_comm
                            if tc < _MAX_TRADES:
                                t_entry[tc] = entry_bar
                                t_exit[tc] = np.int64(i)
                                t_eprice[tc] = entry_price
                                t_xprice[tc] = fp
                                t_pnl[tc] = pnl_val
                                t_comm[tc] = entry_comm + cm
                                t_weight[tc] = prev_weight
                                tc += 1
                            entry_bar = np.int64(-1)
                            entry_comm = 0.0

                elif tv > cv:
                    add = tv - cv
                    if add > cash:
                        add = cash
                    if add > 0.0:
                        fp = op_i * (1.0 + slip_rate)
                        cm = add * comm_rate
                        if cm < min_comm:
                            cm = min_comm
                        if cm < add:
                            bs = (add - cm) / fp
                            if bs > 0.0:
                                filled = True
                                if shares == 0.0:
                                    entry_bar = np.int64(i)
                                    entry_price = fp
                                    entry_comm = cm
                                else:
                                    entry_price = (entry_price * shares + fp * bs) / (shares + bs)
                                    entry_comm += cm
                                shares += bs
                                cash -= add

                if filled:
                    ea = cash + shares * op_i
                    if ea > 0.0:
                        prev_weight = (shares * op_i) / ea
                    else:
                        prev_weight = 0.0

            pv = shares * p_i
            equity_arr[i] = cash + pv
            prev_eq = equity_arr[i - 1]
            if prev_eq > 0.0:
                daily_ret[i] = equity_arr[i] / prev_eq - 1.0

        return (equity_arr, daily_ret,
                t_entry[:tc], t_exit[:tc], t_eprice[:tc],
                t_xprice[:tc], t_pnl[:tc], t_comm[:tc], t_weight[:tc], tc,
                shares, cash, entry_bar, entry_price, entry_comm)

else:
    def jit_simulate_loop(*args, **kwargs):
        raise NotImplementedError("numba not available")
