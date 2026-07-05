/*
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <gnuradio/block.h>
#include <gnuradio/basic_block.h>

#include <ieee802_11/frame_equalizer.h>
#include "frame_equalizer_impl.h"

namespace py = pybind11;

void bind_frame_equalizer(py::module& m)
{
    using ::gr::ieee802_11::frame_equalizer;
    using ::gr::ieee802_11::Equalizer;
    using ::gr::ieee802_11::frame_equalizer_impl;

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
             py::arg("n"))

        // Phase 102: expose d_htsig_null_sc_mask[52] for test inspection.
        // Returns the uint8_t mask parsed from IEEE80211_HTSIG_NULL_SCS env var.
        // Uses static_cast since frame_equalizer::make() always returns a
        // frame_equalizer_impl shared_ptr. We use the public getter
        // get_d_htsig_null_sc_mask() defined in frame_equalizer_impl.h.
        .def_property_readonly("d_htsig_null_sc_mask",
            [](const std::shared_ptr<frame_equalizer>& self) {
                // frame_equalizer::make() always returns a frame_equalizer_impl,
                // so static_cast is safe here. dynamic_pointer_cast would require
                // frame_equalizer_impl's typeinfo to be exported (it is hidden
                // under GNU Radio's default -fvisibility=hidden).
                auto impl = std::static_pointer_cast<frame_equalizer_impl>(self);
                const uint8_t* mask = impl->get_d_htsig_null_sc_mask();
                constexpr int N = frame_equalizer_impl::get_d_htsig_null_sc_mask_size();
                std::vector<int> out;
                out.reserve(N);
                for (int i = 0; i < N; ++i) {
                    out.push_back(static_cast<int>(mask[i]));
                }
                return out;
            });
}
