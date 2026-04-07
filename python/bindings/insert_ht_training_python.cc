#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <gnuradio/block.h>
#include <gnuradio/basic_block.h>
#include <ieee802_11/insert_ht_training.h>

namespace py = pybind11;

void bind_insert_ht_training(py::module& m)
{
    using cls = gr::ieee802_11::insert_ht_training;

    py::class_<cls, gr::block, gr::basic_block, std::shared_ptr<cls>>(
        m,
        "insert_ht_training",
        "Insert HT training (2x vlen=64 symbols) at packet start and fix packet_len tag")
        .def(py::init(&cls::make),
             py::arg("tag_key") = "packet_len")
        .def_static("make",
                    &cls::make,
                    py::arg("tag_key") = "packet_len");
}
