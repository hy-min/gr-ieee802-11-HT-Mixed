#ifndef INCLUDED_IEEE802_11_HT_HEADER_TAGGED_H
#define INCLUDED_IEEE802_11_HT_HEADER_TAGGED_H

#include <ieee802_11/api.h>
#include <gnuradio/block.h>
#include <memory>
#include <string>

namespace gr {
namespace ieee802_11 {

class IEEE802_11_API ht_header_tagged : virtual public gr::block
{
public:
    typedef std::shared_ptr<ht_header_tagged> sptr;

    static sptr make(int rate_field,
                     bool enable_ht,
                     const std::string& len_tag_key,
                     const std::string& encoding_tag_key,
                     const std::string& packet_len_tag_key);
};

} // namespace ieee802_11
} // namespace gr

#endif /* INCLUDED_IEEE802_11_HT_HEADER_TAGGED_H */