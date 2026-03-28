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

using InputArray = nb::ndarray<const double, nb::numpy, nb::ndim<1>>;

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
 * Rolling standard deviation — NaN-safe, two-pass per window.
 */
static nb::object rolling_std(InputArray input, int window, int ddof = 1) {
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
        double mean = 0.0;
        int count = 0;
        for (size_t j = 0; j < w; j++) {
            double v = src[i - w + 1 + j];
            if (!std::isnan(v)) { mean += v; count++; }
        }
        if (count < window) { dst[i] = std::nan(""); continue; }
        mean /= count;

        double var = 0.0;
        for (size_t j = 0; j < w; j++) {
            double v = src[i - w + 1 + j];
            if (!std::isnan(v)) {
                double d = v - mean;
                var += d * d;
            }
        }
        int denom = count - ddof;
        dst[i] = (denom > 0) ? std::sqrt(var / denom) : std::nan("");
    }

    return out;
}

/**
 * Exponential weighted moving average with adjust=True (pandas default).
 * NaN-safe: skip NaN values, maintain weights as if they weren't there.
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
            dst[i] = num / den;
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
        dst[i] = (prev != 0.0 && !std::isnan(prev) && !std::isnan(src[i]))
                 ? (src[i] - prev) / prev
                 : std::nan("");
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
