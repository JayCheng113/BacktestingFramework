cmake_minimum_required(VERSION 3.15...3.30)
project(ez_core_cpp LANGUAGES CXX)

find_package(Python 3.12 COMPONENTS Interpreter Development.Module REQUIRED)
find_package(nanobind CONFIG REQUIRED)

nanobind_add_module(_ts_ops_cpp ts_ops.cpp)

install(TARGETS _ts_ops_cpp LIBRARY DESTINATION ez/core)
