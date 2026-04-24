/**
 * C++ portfolio single-day rebalance: given target weights + prices + current
 * holdings/cash, execute all fills in one pass and return new state.
 *
 * GIL released during the hot loop over symbols.
 */
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/vector.h>
#include <cmath>
#include <vector>
#include <algorithm>

namespace nb = nanobind;

using F64Array = nb::ndarray<const double, nb::numpy, nb::ndim<1>, nb::c_contig>;
using I64Array = nb::ndarray<const int64_t, nb::numpy, nb::ndim<1>, nb::c_contig>;

static nb::object make_f64(size_t n) {
    return nb::module_::import_("numpy").attr("zeros")(n, nb::arg("dtype") = "float64");
}
static nb::object make_i64(size_t n) {
    return nb::module_::import_("numpy").attr("zeros")(n, nb::arg("dtype") = "int64");
}
static double* ptr_f64(nb::object& a) {
    return nb::cast<nb::ndarray<double, nb::numpy, nb::ndim<1>>>(a).data();
}
static int64_t* ptr_i64(nb::object& a) {
    return nb::cast<nb::ndarray<int64_t, nb::numpy, nb::ndim<1>>>(a).data();
}

/**
 * Rebalance one day for a portfolio of N symbols.
 *
 * Inputs (all length N, aligned by symbol index):
 *   target_weights[i]  — desired weight for symbol i (0 = no position)
 *   prices[i]          — execution price (open or close)
 *   raw_prev_close[i]  — for limit-up/down check (0 to skip)
 *   holdings[i]        — current shares held (integer-like)
 *   cash               — current cash
 *   comm_rate           — buy commission rate
 *   sell_comm_rate      — sell commission rate (may include stamp tax)
 *   min_comm            — minimum commission per trade
 *   slip_rate           — slippage
 *   lot_size            — round down to this multiple (100 for A-share)
 *   limit_pct           — price limit (0.10 for 10%; 0 to disable)
 *
 * Returns:
 *   (new_holdings[N], new_cash, trade_count,
 *    trade_symbols[tc], trade_sides[tc], trade_shares[tc],
 *    trade_prices[tc], trade_costs[tc])
 */
static nb::tuple portfolio_rebalance_day(
    F64Array target_weights_in,
    F64Array prices_in,
    F64Array raw_prev_close_in,
    I64Array holdings_in,
    double cash,
    double comm_rate,
    double sell_comm_rate,
    double min_comm,
    double slip_rate,
    int64_t lot_size,
    double limit_pct
) {
    const size_t n = target_weights_in.shape(0);
    const double* tw = target_weights_in.data();
    const double* px = prices_in.data();
    const double* rpc = raw_prev_close_in.data();
    const int64_t* hld = holdings_in.data();

    // Output holdings
    auto new_hld_obj = make_i64(n);
    int64_t* new_hld = ptr_i64(new_hld_obj);

    // Trade accumulators (worst case: 2*N trades for sell-all + buy-all)
    std::vector<int64_t> t_sym;
    std::vector<int64_t> t_side;   // 0=buy, 1=sell
    std::vector<int64_t> t_shares;
    std::vector<double> t_price;
    std::vector<double> t_cost;
    t_sym.reserve(n);

    double new_cash = cash;

    {
        nb::gil_scoped_release release;

        // Copy holdings
        for (size_t i = 0; i < n; ++i) new_hld[i] = hld[i];

        // Compute equity
        double equity = new_cash;
        for (size_t i = 0; i < n; ++i) {
            if (!std::isnan(px[i]) && px[i] > 0)
                equity += static_cast<double>(new_hld[i]) * px[i];
        }

        // Phase 1: Sells first (free up cash)
        for (size_t i = 0; i < n; ++i) {
            if (std::isnan(px[i]) || px[i] <= 0) continue;
            double price = px[i];

            // Limit-down check: can't sell if at lower limit
            if (limit_pct > 0 && rpc[i] > 0) {
                double lower = rpc[i] * (1.0 - limit_pct);
                if (price <= lower + 1e-6) continue;
            }

            double target_value = equity * tw[i];
            double current_value = static_cast<double>(new_hld[i]) * price;
            if (target_value >= current_value) continue;  // no sell needed

            double sell_value = current_value - target_value;
            int64_t sell_shares = static_cast<int64_t>(sell_value / price);
            if (lot_size > 1) sell_shares = (sell_shares / lot_size) * lot_size;
            if (sell_shares <= 0) continue;
            if (sell_shares > new_hld[i]) sell_shares = new_hld[i];

            double fp = price * (1.0 - slip_rate);
            if (fp < 0) fp = 0;
            double val = static_cast<double>(sell_shares) * fp;
            double cm = val * sell_comm_rate;
            if (cm < min_comm) cm = min_comm;
            if (cm > val) cm = val;

            new_hld[i] -= sell_shares;
            new_cash += val - cm;

            t_sym.push_back(static_cast<int64_t>(i));
            t_side.push_back(1);
            t_shares.push_back(sell_shares);
            t_price.push_back(fp);
            t_cost.push_back(cm);
        }

        // Recompute equity after sells
        equity = new_cash;
        for (size_t i = 0; i < n; ++i) {
            if (!std::isnan(px[i]) && px[i] > 0)
                equity += static_cast<double>(new_hld[i]) * px[i];
        }

        // Phase 2: Buys (use freed cash)
        for (size_t i = 0; i < n; ++i) {
            if (std::isnan(px[i]) || px[i] <= 0) continue;
            double price = px[i];

            // Limit-up check: can't buy if at upper limit
            if (limit_pct > 0 && rpc[i] > 0) {
                double upper = rpc[i] * (1.0 + limit_pct);
                if (price >= upper - 1e-6) continue;
            }

            double target_value = equity * tw[i];
            double current_value = static_cast<double>(new_hld[i]) * price;
            if (target_value <= current_value) continue;

            double buy_amount = std::min(target_value - current_value, new_cash);
            if (buy_amount <= 0) continue;

            double fp = price * (1.0 + slip_rate);
            double cm = buy_amount * comm_rate;
            if (cm < min_comm) cm = min_comm;
            if (cm >= buy_amount) continue;

            double raw_shares = (buy_amount - cm) / fp;
            int64_t buy_shares = static_cast<int64_t>(raw_shares);
            if (lot_size > 1) buy_shares = (buy_shares / lot_size) * lot_size;
            if (buy_shares <= 0) continue;

            double actual_cost = static_cast<double>(buy_shares) * fp;
            double actual_cm = actual_cost * comm_rate;
            if (actual_cm < min_comm) actual_cm = min_comm;

            new_hld[i] += buy_shares;
            new_cash -= (actual_cost + actual_cm);

            t_sym.push_back(static_cast<int64_t>(i));
            t_side.push_back(0);
            t_shares.push_back(buy_shares);
            t_price.push_back(fp);
            t_cost.push_back(actual_cm);
        }
    }
    // GIL re-acquired

    size_t tc = t_sym.size();
    auto ts_obj = make_i64(tc); auto td_obj = make_i64(tc);
    auto tsh_obj = make_i64(tc); auto tp_obj = make_f64(tc);
    auto tc_obj = make_f64(tc);
    if (tc > 0) {
        int64_t* ts = ptr_i64(ts_obj); int64_t* td = ptr_i64(td_obj);
        int64_t* tsh = ptr_i64(tsh_obj); double* tp = ptr_f64(tp_obj);
        double* tcc = ptr_f64(tc_obj);
        for (size_t j = 0; j < tc; ++j) {
            ts[j] = t_sym[j]; td[j] = t_side[j];
            tsh[j] = t_shares[j]; tp[j] = t_price[j]; tcc[j] = t_cost[j];
        }
    }

    return nb::make_tuple(
        new_hld_obj, new_cash, static_cast<int64_t>(tc),
        ts_obj, td_obj, tsh_obj, tp_obj, tc_obj
    );
}

NB_MODULE(_portfolio_rebalance_cpp, m) {
    m.doc() = "C++ portfolio single-day rebalance with GIL release.";
    m.def("portfolio_rebalance_day", &portfolio_rebalance_day,
        nb::arg("target_weights"), nb::arg("prices"), nb::arg("raw_prev_close"),
        nb::arg("holdings"), nb::arg("cash"),
        nb::arg("comm_rate"), nb::arg("sell_comm_rate"), nb::arg("min_comm"),
        nb::arg("slip_rate"), nb::arg("lot_size"), nb::arg("limit_pct"),
        "Execute one portfolio rebalance day in C++ with GIL released."
    );
}
