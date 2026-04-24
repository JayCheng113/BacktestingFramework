/**
 * C++ vectorized backtest simulation loop for ez-trading.
 *
 * Replaces the Python for-loop in VectorizedBacktestEngine._simulate
 * for binary-signal SimpleMatcher/SlippageMatcher strategies.
 * GIL is released during the hot loop.
 *
 * Input: numpy float64 arrays (prices, open_prices, weights) + scalar params.
 * Output: tuple of numpy arrays (equity, daily_ret, trade records, final state).
 */
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <cmath>
#include <vector>
#include <algorithm>

namespace nb = nanobind;

using InputArray = nb::ndarray<const double, nb::numpy, nb::ndim<1>, nb::c_contig>;

static nb::object make_array(size_t n) {
    auto np = nb::module_::import_("numpy");
    return np.attr("empty")(n, nb::arg("dtype") = "float64");
}

static nb::object make_int_array(size_t n) {
    auto np = nb::module_::import_("numpy");
    return np.attr("empty")(n, nb::arg("dtype") = "int64");
}

static double* get_f64(nb::object& arr) {
    return nb::cast<nb::ndarray<double, nb::numpy, nb::ndim<1>>>(arr).data();
}

static int64_t* get_i64(nb::object& arr) {
    return nb::cast<nb::ndarray<int64_t, nb::numpy, nb::ndim<1>>>(arr).data();
}

struct TradeRecord {
    int64_t entry_bar;
    int64_t exit_bar;
    double entry_price;
    double exit_price;
    double pnl;
    double commission;
    double weight;
};

struct SimState {
    double shares;
    double cash;
    int64_t entry_bar;
    double entry_price;
    double entry_comm;
};

static nb::tuple simulate_loop(
    InputArray prices_in,
    InputArray open_prices_in,
    InputArray weights_in,
    double capital,
    double comm_rate,
    double sell_comm_rate,
    double min_comm,
    double slip_rate
) {
    const size_t n = prices_in.shape(0);
    const double* prices = prices_in.data();
    const double* open_prices = open_prices_in.data();
    const double* weights = weights_in.data();

    // Allocate output arrays while GIL is held
    auto eq_obj = make_array(n);
    auto dr_obj = make_array(n);
    double* equity = get_f64(eq_obj);
    double* daily_ret = get_f64(dr_obj);

    // Trade records (dynamic, collected during simulation)
    std::vector<TradeRecord> trades;
    trades.reserve(1024);

    // Final state for terminal liquidation
    SimState final_state{0.0, capital, -1, 0.0, 0.0};

    // --- Release GIL for the hot loop ---
    {
        nb::gil_scoped_release release;

        equity[0] = capital;
        daily_ret[0] = 0.0;

        double cash = capital;
        double shares = 0.0;
        double prev_weight = 0.0;
        int64_t entry_bar = -1;
        double entry_price = 0.0;
        double entry_comm = 0.0;

        for (size_t i = 1; i < n; ++i) {
            double p_i = prices[i];
            double op_i = open_prices[i];

            // NaN guard
            if (std::isnan(p_i) || std::isnan(op_i)) {
                equity[i] = equity[i - 1];
                daily_ret[i] = 0.0;
                continue;
            }

            double tw = weights[i];
            double diff = tw - prev_weight;

            if (diff > 1e-3 || diff < -1e-3) {
                double eq_now = cash + shares * op_i;
                double tv = eq_now * tw;
                double cv = shares * op_i;
                bool filled = false;

                if (tv < cv && shares > 0.0) {
                    // Sell
                    double ss = (tw == 0.0) ? shares : (cv - tv) / op_i;
                    double fp = op_i * (1.0 - slip_rate);
                    if (fp < 0.0) fp = 0.0;
                    double val = ss * fp;
                    double cm = val * sell_comm_rate;
                    if (cm < min_comm) cm = min_comm;
                    if (cm > val) cm = val;

                    if (ss > 0.0) {
                        filled = true;
                        cash += val - cm;
                        double old = shares;
                        shares -= ss;
                        if (shares < 1e-10) shares = 0.0;

                        // Record trade on full close
                        if (old > 0.0 && shares < 1e-10 && entry_bar >= 0) {
                            double pnl = (fp - entry_price) * old - cm - entry_comm;
                            trades.push_back({
                                entry_bar, static_cast<int64_t>(i),
                                entry_price, fp, pnl,
                                entry_comm + cm, prev_weight
                            });
                            entry_bar = -1;
                            entry_comm = 0.0;
                        }
                    }
                } else if (tv > cv) {
                    // Buy
                    double add = std::min(tv - cv, cash);
                    if (add > 0.0) {
                        double fp = op_i * (1.0 + slip_rate);
                        double cm = add * comm_rate;
                        if (cm < min_comm) cm = min_comm;
                        if (cm < add) {
                            double bs = (add - cm) / fp;
                            if (bs > 0.0) {
                                filled = true;
                                if (shares == 0.0) {
                                    entry_bar = static_cast<int64_t>(i);
                                    entry_price = fp;
                                    entry_comm = cm;
                                } else {
                                    entry_price = (entry_price * shares + fp * bs) / (shares + bs);
                                    entry_comm += cm;
                                }
                                shares += bs;
                                cash -= add;
                            }
                        }
                    }
                }

                if (filled) {
                    double ea = cash + shares * op_i;
                    prev_weight = (ea > 0.0) ? (shares * op_i) / ea : 0.0;
                }
            }

            double pv = shares * prices[i];
            equity[i] = cash + pv;
            double prev_eq = equity[i - 1];
            daily_ret[i] = (prev_eq > 0.0) ? (equity[i] / prev_eq - 1.0) : 0.0;
        }

        // Save final state
        final_state = {shares, cash, entry_bar, entry_price, entry_comm};
    }
    // --- GIL re-acquired ---

    // Build trade record arrays
    size_t tc = trades.size();
    auto te_obj = make_int_array(tc);
    auto tx_obj = make_int_array(tc);
    auto tep_obj = make_array(tc);
    auto txp_obj = make_array(tc);
    auto tpnl_obj = make_array(tc);
    auto tcm_obj = make_array(tc);
    auto tw_obj = make_array(tc);

    if (tc > 0) {
        int64_t* te = get_i64(te_obj);
        int64_t* tx = get_i64(tx_obj);
        double* tep = get_f64(tep_obj);
        double* txp = get_f64(txp_obj);
        double* tpnl = get_f64(tpnl_obj);
        double* tcm = get_f64(tcm_obj);
        double* twt = get_f64(tw_obj);
        for (size_t j = 0; j < tc; ++j) {
            te[j] = trades[j].entry_bar;
            tx[j] = trades[j].exit_bar;
            tep[j] = trades[j].entry_price;
            txp[j] = trades[j].exit_price;
            tpnl[j] = trades[j].pnl;
            tcm[j] = trades[j].commission;
            twt[j] = trades[j].weight;
        }
    }

    return nb::make_tuple(
        eq_obj, dr_obj,
        te_obj, tx_obj, tep_obj, txp_obj, tpnl_obj, tcm_obj, tw_obj,
        static_cast<int64_t>(tc),
        final_state.shares, final_state.cash,
        final_state.entry_bar, final_state.entry_price, final_state.entry_comm
    );
}

NB_MODULE(_simulate_cpp, m) {
    m.doc() = "C++ backtest simulation loop with GIL release.";
    m.def("simulate_loop", &simulate_loop,
        nb::arg("prices"), nb::arg("open_prices"), nb::arg("weights"),
        nb::arg("capital"),
        nb::arg("comm_rate"), nb::arg("sell_comm_rate"),
        nb::arg("min_comm"), nb::arg("slip_rate"),
        "Run the full simulation loop in C++ with GIL released.\n"
        "Returns (equity, daily_ret, trade_entry_bars, trade_exit_bars, "
        "trade_entry_prices, trade_exit_prices, trade_pnls, trade_comms, "
        "trade_weights, trade_count, final_shares, final_cash, "
        "final_entry_bar, final_entry_price, final_entry_comm)."
    );
}
