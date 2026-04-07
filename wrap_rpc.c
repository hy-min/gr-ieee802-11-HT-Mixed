#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>

// Constructor function runs before main()
__attribute__((constructor)) void disable_rpc_init(void) {
    // Write to stderr so we can see it's loaded
    fprintf(stderr, "[wrap_rpc] LD_PRELOAD wrapper loaded, disabling RPC...\n");

    // Unset any existing RPC environment variables
    unsetenv("GR_RPC_SERVER_ENABLE");
    unsetenv("GR_CONF_CONTROLPORT_ON");
    unsetenv("GR_RPC_ENABLE");
    unsetenv("GR_CONTROLPORT_ON");
    unsetenv("GR_RPC_PORT");
    unsetenv("GR_CONF_CONTROLPORT");
    unsetenv("GR_PERFORMANCE_COUNTERS_ON");
    unsetenv("GR_PREFER_TCP_FOR_RPC");
    unsetenv("GR_RPC_THRIFT");

    // Set them all to False/0
    setenv("GR_CONF_CONTROLPORT_ON", "False", 1);
    setenv("GR_RPC_ENABLE", "False", 1);
    setenv("GR_RPC_SERVER_ENABLE", "False", 1);
    setenv("GR_RPC_PORT", "0", 1);
    setenv("GR_CONTROLPORT_ON", "False", 1);
    setenv("GR_CONF_CONTROLPORT", "False", 1);
    setenv("GR_PERFORMANCE_COUNTERS_ON", "False", 1);
    setenv("GR_PREFER_TCP_FOR_RPC", "False", 1);
    setenv("GR_RPC_THRIFT", "False", 1);

    // Also set some GNU Radio config file environment variables
    setenv("GNURADIO_RUNTIME_DIR", "/tmp/gnuradio_no_rpc", 1);

    fprintf(stderr, "[wrap_rpc] RPC environment variables set.\n");

    // Optional: Try to prevent ControlPort/RPC module loading
    // This might help if GNU Radio checks environment variables later
    char *ld_preload = getenv("LD_PRELOAD");
    if (ld_preload) {
        fprintf(stderr, "[wrap_rpc] LD_PRELOAD=%s\n", ld_preload);
    }
}