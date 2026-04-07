#!/usr/bin/env python3
"""
Simple flow graph test with RPC disabled.
"""
import os
import sys

# Disable RPC/ControlPort before importing GNU Radio
os.environ['GR_CONF_CONTROLPORT_ON'] = 'False'
os.environ['GR_RPC_ENABLE'] = 'False'
os.environ['GR_RPC_SERVER_ENABLE'] = 'False'
os.environ['GR_RPC_SERVER_PORT'] = '0'
os.environ['GR_CONTROLPORT_ON'] = 'False'
os.environ['GNURADIO_RUNTIME_DIR'] = '/tmp/gnuradio_test_no_rpc'

print("=== Simple Flow Graph Test with RPC Disabled ===")

try:
    from gnuradio import gr, blocks
    import ieee802_11

    print("✓ Imports successful")

    # Create a simple flow graph
    print("Creating top block...")
    tb = gr.top_block()

    # Create a source with dummy data
    print("Creating vector source...")
    src_data = [complex(0, 0)] * 520  # 10 symbols * 52 subcarriers
    src = blocks.vector_source_c(src_data, False, 1, [])

    # Create decode_mac
    print("Creating decode_mac block...")
    decode = ieee802_11.decode_mac(log=True, debug=True)

    # Create a null sink (decode_mac has no output ports, only message port)
    print("Creating null sink...")
    sink = blocks.null_sink(gr.sizeof_gr_complex)

    # Connect source to decode_mac (decode_mac will output messages)
    print("Connecting blocks...")
    tb.connect(src, decode)

    # Also connect decode_mac output port 0 to sink if it exists
    # (decode_mac has no output ports, so we can't connect it)

    print("✓ Flow graph created successfully")
    print("\nNote: decode_mac is a message-passing block with no stream output.")
    print("It will output messages via its message port when it receives tagged frames.")

    # Try to run the flow graph for a short time
    print("\nAttempting to run flow graph...")

    # We need to add a message port handler to see if decode_mac works
    def handle_message(msg):
        print(f"Received message: {msg}")

    # Get the message port
    msg_port = decode.message_port_register_out("out")

    # Try to start the flow graph (without running it)
    print("Flow graph construction successful.")
    print("\n✓ TEST PASSED: Flow graph can be created with RPC disabled")

    # Clean up
    del tb
    print("\nDone.")

except Exception as e:
    print(f"\n✗ ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)