/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <gnuradio/block.h>
#include <gnuradio/basic_block.h>

#include <ieee802_11/frame_equalizer.h>

namespace py = pybind11;

void bind_frame_equalizer(py::module& m)
{
    using ::gr::ieee802_11::frame_equalizer;
    using ::gr::ieee802_11::Equalizer;

    py::class_<frame_equalizer,
               gr::block,                 // ✅ 关键：必须继承 gr::block
               gr::basic_block,           // ✅ 建议也显式写上 basic_block
               std::shared_ptr<frame_equalizer>>(m, "frame_equalizer")

        .def(py::init(&frame_equalizer::make),
             py::arg("algo"),
             py::arg("freq"),
             py::arg("bw"),
             py::arg("log") = false,
             py::arg("debug") = false)

        .def("set_algorithm", &frame_equalizer::set_algorithm, py::arg("algo"))
        .def("set_bandwidth", &frame_equalizer::set_bandwidth, py::arg("bw"))
        .def("set_frequency", &frame_equalizer::set_frequency, py::arg("freq"))

        // 你新增的接口：用于跳过 HT header symbols
        .def("set_extra_header_symbols",
             &frame_equalizer::set_extra_header_symbols,
             py::arg("n"));
}
