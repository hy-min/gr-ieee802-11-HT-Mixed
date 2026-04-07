/*
 * Copyright 2021 Free Software Foundation, Inc.
 *
 * This file is part of GNU Radio
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 */

#include <pybind11/complex.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

namespace py = pybind11;

#include <ieee802_11/ht_header_tagged.h>
// pydoc.h is automatically generated in the build directory
#include <ht_header_tagged_pydoc.h>

void bind_ht_header_tagged(py::module& m)
{

    using ht_header_tagged = ::gr::ieee802_11::ht_header_tagged;

    py::class_<ht_header_tagged, gr::block, std::shared_ptr<ht_header_tagged>>(m, "ht_header_tagged", D(ht_header_tagged))

        .def(py::init(&ht_header_tagged::make),
           D(ht_header_tagged,make),
           py::arg("rate_field") = 13,
           py::arg("enable_ht") = true,
           py::arg("len_tag_key") = "psdu_len",
           py::arg("encoding_tag_key") = "encoding",
           py::arg("packet_len_tag_key") = "packet_len")

        ;

}
