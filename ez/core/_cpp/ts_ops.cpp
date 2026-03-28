/**
 * C++ time series operations for ez-trading.
 *
 * Operates on numpy float64 arrays, returns numpy arrays.
 * NaN handling: NaN values are skipped in accumulations, matching pandas behavior.
 * Output is NaN where valid count < min_periods (= window).
 */
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <cmath>
#include <algorithm>

namespace nb = nanobind;

using InputArray = nb::ndarray<const double, nb::numpy, nb::ndim<1>, nb::c_contig>;

static nb::object make_output(size_t n) {
    auto np = nb::module_::import_("numpy");
    return np.attr("empty")(n, nb::arg("dtype") = "float64");
}

static double* get_data(nb::object& arr) {
    auto a = nb::cast<nb::ndarray<double, nb::numpy, nb::ndim<1>>>(arr);
    return a.data();
}

/**
 * Simple moving average: O(n*window) — handles NaN by counting valid values.
 */
static nb::object rolling_mean(InputArray input, int window) {
    if (window <= 0) throw nb::value_error("window must be positive");
    size_t n = input.shape(0);
    const double* src = input.data();
    size_t w = static_cast<size_t>(window);

    auto out = make_output(n);
    double* dst = get_data(out);

    for (size_t i = 0; i < n; i++) {
        if (i < w - 1) {
            dst[i] = std::nan("");
            continue;
        }
        double sum = 0.0;
        int count = 0;
        for (size_t j = 0; j < w; j++) {
            double v = src[i - w + 1 + j];
            if (!std::isnan(v)) {
                sum += v;
                count++;
            }
        }
        // min_periods = window: require ALL values to be valid
        dst[i] = (count == window) ? sum / window : std::nan("");
    }

    return out;
}

/**
 * Rolling standard deviation — Welford online O(n) algorithm.
 *
 * Uses sliding-window Welford update: when both entering and leaving values
 * are non-NaN, update mean/M2 in O(1). Falls back to O(w) recomputation
 * only at NaN boundaries (min_periods = window).
 */
static nb::object rolling_std(InputArray input, int window, int ddof = 1) {
    if (window <= 0) throw nb::value_error("window must be positive");
    size_t n = input.shape(0);
    const double* src = input.data();
    size_t w = static_cast<size_t>(window);

    auto out = make_output(n);
    double* dst = get_data(out);

    // Leading NaN
    for (size_t i = 0; i + 1 < w && i < n; i++) {
        dst[i] = std::nan("");
    }
    if (w > n) return out;

    // Count NaN values in initial window
    int nan_count = 0;
    for (size_t j = 0; j < w; j++) {
        if (std::isnan(src[j])) nan_count++;
    }

    double mean = 0.0, M2 = 0.0;
    bool welford_valid = false;

    // Recompute Welford state from scratch for window starting at `start`
    auto recompute = [&](size_t start) {
        mean = 0.0;
        M2 = 0.0;
        for (size_t j = 0; j < w; j++) {
            double v = src[start + j];
            double k = static_cast<double>(j + 1);
            double delta = v - mean;
            mean += delta / k;
            M2 += delta * (v - mean);
        }
        welford_valid = true;
    };

    // First complete window
    if (nan_count == 0) {
        recompute(0);
        int denom = static_cast<int>(w) - ddof;
        dst[w - 1] = (denom > 0) ? std::sqrt(std::max(M2 / denom, 0.0)) : std::nan("");
    } else {
        dst[w - 1] = std::nan("");
    }

    // Slide window: O(1) per step when no NaN at boundaries.
    // Periodic recompute every `w` steps to bound floating-point drift
    // on large-magnitude data (e.g., 1e12 + noise).
    int steps_since_recompute = 0;

    for (size_t i = w; i < n; i++) {
        double new_val = src[i];
        double old_val = src[i - w];
        bool old_nan = std::isnan(old_val);
        bool new_nan = std::isnan(new_val);

        if (old_nan) nan_count--;
        if (new_nan) nan_count++;

        if (nan_count > 0) {
            dst[i] = std::nan("");
            welford_valid = false;
            steps_since_recompute = 0;
            continue;
        }

        // All values in window are valid
        if (!welford_valid || old_nan || steps_since_recompute >= (int)w) {
            // Recompute from scratch: NaN boundary or periodic drift correction
            recompute(i - w + 1);
            steps_since_recompute = 0;
        } else {
            // O(1) sliding Welford update:
            // new_mean = old_mean + (new_val - old_val) / w
            // new_M2 = old_M2 + (new_val - old_val) * (new_val + old_val - old_mean - new_mean)
            double old_mean = mean;
            mean = old_mean + (new_val - old_val) / static_cast<double>(w);
            M2 += (new_val - old_val) * (new_val + old_val - old_mean - mean);
            if (M2 < 0.0) M2 = 0.0;  // clamp floating-point noise
            steps_since_recompute++;
        }

        int denom = static_cast<int>(w) - ddof;
        dst[i] = (denom > 0) ? std::sqrt(std::max(M2 / denom, 0.0)) : std::nan("");
    }

    return out;
}

/**
 * Exponential weighted moving average with adjust=True (pandas default).
 * NaN-safe: skip NaN values, maintain weights as if they weren't there.
 *
 * When decay==0 (span=1), NaN causes den to reach 0. In that case pandas
 * keeps the last valid weighted_avg — we match this with last_valid.
 */
static nb::object ewm_mean(InputArray input, int span) {
    if (span <= 0) throw nb::value_error("span must be positive");
    size_t n = input.shape(0);
    const double* src = input.data();
    double alpha = 2.0 / (span + 1.0);
    double decay = 1.0 - alpha;

    auto out = make_output(n);
    double* dst = get_data(out);

    size_t min_p = static_cast<size_t>(span);
    double num = 0.0, den = 0.0;
    int valid_count = 0;
    double last_valid = std::nan("");

    for (size_t i = 0; i < n; i++) {
        if (!std::isnan(src[i])) {
            num = src[i] + decay * num;
            den = 1.0 + decay * den;
            valid_count++;
        } else {
            // Decay existing weights but don't add new observation
            num = decay * num;
            den = decay * den;
        }

        if (valid_count >= (int)min_p && den > 0) {
            last_valid = num / den;
            dst[i] = last_valid;
        } else if (valid_count >= (int)min_p && !std::isnan(last_valid)) {
            // den collapsed to 0 (e.g., span=1 + NaN) but we have a prior
            // valid EWM — pandas keeps the last weighted_avg in this case
            dst[i] = last_valid;
        } else {
            dst[i] = std::nan("");
        }
    }

    return out;
}

/**
 * First difference: out[i] = x[i] - x[i-periods].
 */
static nb::object diff(InputArray input, int periods = 1) {
    if (periods <= 0) throw nb::value_error("periods must be positive");
    size_t n = input.shape(0);
    const double* src = input.data();
    size_t p = static_cast<size_t>(periods);

    auto out = make_output(n);
    double* dst = get_data(out);

    for (size_t i = 0; i < std::min(n, p); i++) dst[i] = std::nan("");
    for (size_t i = p; i < n; i++) dst[i] = src[i] - src[i - p];

    return out;
}

/**
 * Percentage change: out[i] = (x[i] - x[i-periods]) / x[i-periods].
 */
static nb::object pct_change(InputArray input, int periods = 1) {
    if (periods <= 0) throw nb::value_error("periods must be positive");
    size_t n = input.shape(0);
    const double* src = input.data();
    size_t p = static_cast<size_t>(periods);

    auto out = make_output(n);
    double* dst = get_data(out);

    for (size_t i = 0; i < std::min(n, p); i++) dst[i] = std::nan("");
    for (size_t i = p; i < n; i++) {
        double prev = src[i - p];
        if (std::isnan(prev) || std::isnan(src[i])) {
            dst[i] = std::nan("");
        } else {
            // IEEE 754: 1.0/0.0 = inf, -1.0/0.0 = -inf, 0.0/0.0 = NaN — matches pandas
            dst[i] = (src[i] - prev) / prev;
        }
    }

    return out;
}


NB_MODULE(_ts_ops_cpp, m) {
    m.doc() = "C++ accelerated time series operations for ez-trading";
    m.def("rolling_mean", &rolling_mean, nb::arg("input"), nb::arg("window"));
    m.def("rolling_std", &rolling_std, nb::arg("input"), nb::arg("window"), nb::arg("ddof") = 1);
    m.def("ewm_mean", &ewm_mean, nb::arg("input"), nb::arg("span"));
    m.def("diff", &diff, nb::arg("input"), nb::arg("periods") = 1);
    m.def("pct_change", &pct_change, nb::arg("input"), nb::arg("periods") = 1);
}
