#include <pybind11/pybind11.h>

namespace py = pybind11;

// forward declarations (all blocks use global bind_* functions)
void bind_chunks_to_symbols(py::module& m);
void bind_constellations(py::module& m);
void bind_decode_mac(py::module& m);
void bind_ether_encap(py::module& m);
void bind_extract_csi(py::module& m);
void bind_frame_equalizer(py::module& m);
void bind_ht_symbol_splitter(py::module& m);
void bind_mac(py::module& m);
void bind_mapper(py::module& m);
void bind_parse_mac(py::module& m);
void bind_signal_field(py::module& m);
void bind_ht_header_tagged(py::module& m);
void bind_sync_long(py::module& m);
void bind_sync_short(py::module& m);
void bind_equalizer(py::module& m);

// ✅ Make insert_ht_training consistent with others: global binder
void bind_insert_ht_training(py::module& m);

PYBIND11_MODULE(ieee802_11_python, m)
{
    // Ensure GNU Radio base types are registered first
    py::module_::import("gnuradio.gr");
    py::module_::import("gnuradio.digital");

    bind_equalizer(m);
    bind_constellations(m);
    bind_chunks_to_symbols(m);
    bind_mapper(m);
    bind_sync_short(m);
    bind_sync_long(m);
    bind_signal_field(m);
    bind_ht_header_tagged(m);
    bind_ht_symbol_splitter(m);
    bind_frame_equalizer(m);
    bind_decode_mac(m);
    bind_mac(m);
    bind_parse_mac(m);
    bind_extract_csi(m);
    bind_ether_encap(m);

    // ✅ Call insert_ht_training binder like all others
    bind_insert_ht_training(m);
}
