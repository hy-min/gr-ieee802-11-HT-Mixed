#include <pybind11/pybind11.h>
#include <ieee802_11/frame_equalizer.h>

namespace py = pybind11;

void bind_equalizer(py::module& m)
{
    py::enum_<gr::ieee802_11::Equalizer>(m, "Equalizer")
        .value("COMB", gr::ieee802_11::COMB)
        .value("LS",   gr::ieee802_11::LS)
        .value("LMS",  gr::ieee802_11::LMS)
        .value("STA",  gr::ieee802_11::STA)
        .export_values();

    // 兼容旧脚本：ieee802_11.LS / ieee802_11.LMS ...
    m.attr("COMB") = m.attr("Equalizer").attr("COMB");
    m.attr("LS")   = m.attr("Equalizer").attr("LS");
    m.attr("LMS")  = m.attr("Equalizer").attr("LMS");
    m.attr("STA")  = m.attr("Equalizer").attr("STA");
}
