/**
 * C++ time series operations for ez-trading.
 *
 * Operates on numpy float64 arrays, returns numpy arrays.
 * NaN for positions where window is insufficient (matches pandas).
 */
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <cmath>
#include <algorithm>
#include <vector>

namespace nb = nanobind;

using InputArray = nb::ndarray<const double, nb::numpy, nb::ndim<1>>;

// Helper: create a numpy float64 array of size n
static nb::object make_output(size_t n) {
    auto np = nb::module_::import_("numpy");
    return np.attr("empty")(n, nb::arg("dtype") = "float64");
}

static double* get_data(nb::object& arr) {
    auto a = nb::cast<nb::ndarray<double, nb::numpy, nb::ndim<1>>>(arr);
    return a.data();
}

/**
 * Simple moving average: O(n) sliding window.
 */
static nb::object rolling_mean(InputArray input, int window) {
    size_t n = input.shape(0);
    const double* src = input.data();

    auto out = make_output(n);
    double* dst = get_data(out);

    size_t w = static_cast<size_t>(window);
    for (size_t i = 0; i < std::min(n, w - 1); i++) {
        dst[i] = std::nan("");
    }

    if (n < w) return out;

    double sum = 0.0;
    for (size_t i = 0; i < w; i++) sum += src[i];
    dst[w - 1] = sum / window;

    for (size_t i = w; i < n; i++) {
        sum += src[i] - src[i - w];
        dst[i] = sum / window;
    }

    return out;
}

/**
 * Rolling standard deviation.
 * Two-pass per window. O(n*window) but window is typically small (5-60).
 */
static nb::object rolling_std(InputArray input, int window, int ddof = 1) {
    size_t n = input.shape(0);
    const double* src = input.data();

    auto out = make_output(n);
    double* dst = get_data(out);

    size_t w = static_cast<size_t>(window);
    for (size_t i = 0; i < std::min(n, w - 1); i++) {
        dst[i] = std::nan("");
    }

    if (n < w) return out;

    for (size_t i = w - 1; i < n; i++) {
        double mean = 0.0;
        for (size_t j = 0; j < w; j++) mean += src[i - w + 1 + j];
        mean /= window;

        double var = 0.0;
        for (size_t j = 0; j < w; j++) {
            double d = src[i - w + 1 + j] - mean;
            var += d * d;
        }
        int denom = window - ddof;
        dst[i] = (denom > 0) ? std::sqrt(var / denom) : std::nan("");
    }

    return out;
}

/**
 * Exponential weighted moving average with adjust=True (pandas default).
 *
 * Incremental formula: num = alpha*x + (1-alpha)*num, den = 1 + (1-alpha)*den
 * Result = num / den
 */
static nb::object ewm_mean(InputArray input, int span) {
    size_t n = input.shape(0);
    const double* src = input.data();
    double alpha = 2.0 / (span + 1.0);
    double decay = 1.0 - alpha;

    auto out = make_output(n);
    double* dst = get_data(out);

    size_t min_p = static_cast<size_t>(span);
    for (size_t i = 0; i < std::min(n, min_p - 1); i++) {
        dst[i] = std::nan("");
    }

    if (n < min_p) return out;

    double num = 0.0, den = 0.0;
    for (size_t i = 0; i < n; i++) {
        num = src[i] + decay * num;    // NO alpha multiply — pandas adjust=True
        den = 1.0 + decay * den;
        if (i >= min_p - 1) {
            dst[i] = num / den;
        }
    }

    return out;
}

/**
 * First difference: out[i] = x[i] - x[i-periods].
 */
static nb::object diff(InputArray input, int periods = 1) {
    size_t n = input.shape(0);
    const double* src = input.data();

    auto out = make_output(n);
    double* dst = get_data(out);

    size_t p = static_cast<size_t>(periods);
    for (size_t i = 0; i < std::min(n, p); i++) dst[i] = std::nan("");
    for (size_t i = p; i < n; i++) dst[i] = src[i] - src[i - p];

    return out;
}

/**
 * Percentage change: out[i] = (x[i] - x[i-periods]) / x[i-periods].
 */
static nb::object pct_change(InputArray input, int periods = 1) {
    size_t n = input.shape(0);
    const double* src = input.data();

    auto out = make_output(n);
    double* dst = get_data(out);

    size_t p = static_cast<size_t>(periods);
    for (size_t i = 0; i < std::min(n, p); i++) dst[i] = std::nan("");
    for (size_t i = p; i < n; i++) {
        double prev = src[i - p];
        dst[i] = (prev != 0.0) ? (src[i] - prev) / prev : std::nan("");
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
