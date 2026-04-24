cmake_minimum_required(VERSION 3.15...3.30)
project(ez_core_cpp LANGUAGES CXX)

find_package(Python 3.12 COMPONENTS Interpreter Development.Module REQUIRED)
find_package(nanobind CONFIG REQUIRED)

nanobind_add_module(_ts_ops_cpp ts_ops.cpp)
nanobind_add_module(_simulate_cpp simulate_loop.cpp)
nanobind_add_module(_portfolio_rebalance_cpp portfolio_rebalance.cpp)

install(TARGETS _ts_ops_cpp _simulate_cpp _portfolio_rebalance_cpp LIBRARY DESTINATION ez/core)
