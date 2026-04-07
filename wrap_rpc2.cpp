#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <dlfcn.h>
#include <string.h>

// Forward declaration (we don't need the actual class definition)
class rpcserver_booter_base;

namespace rpcmanager {
    // Original function pointer
    static void (*original_register_booter)(rpcserver_booter_base*) = nullptr;

    // Our wrapper function
    void register_booter(rpcserver_booter_base* booter) {
        static bool first_call = true;

        fprintf(stderr, "[wrap_rpc2] rpcmanager::register_booter called\n");

        if (first_call) {
            fprintf(stderr, "[wrap_rpc2] First registration, allowing it\n");
            if (original_register_booter) {
                original_register_booter(booter);
            } else {
                fprintf(stderr, "[wrap_rpc2] Warning: original function not available\n");
            }
            first_call = false;
        } else {
            fprintf(stderr, "[wrap_rpc2] Subsequent registration ignored (already registered)\n");
            // Do nothing - ignore duplicate registration
        }
    }
}

// Constructor - runs before main
__attribute__((constructor)) void init_wrapper() {
    fprintf(stderr, "[wrap_rpc2] LD_PRELOAD wrapper initialized\n");

    // Set environment variables
    setenv("GR_CONF_CONTROLPORT_ON", "False", 1);
    setenv("GR_RPC_ENABLE", "False", 1);
    setenv("GR_RPC_SERVER_ENABLE", "False", 1);
    setenv("GR_RPC_PORT", "0", 1);
    setenv("GR_CONTROLPORT_ON", "False", 1);
    setenv("GR_CONF_CONTROLPORT", "False", 1);

    // Get original function
    rpcmanager::original_register_booter = (void(*)(rpcserver_booter_base*))dlsym(RTLD_NEXT,
        "_ZN10rpcmanager15register_booterEP21rpcserver_booter_base");

    if (rpcmanager::original_register_booter) {
        fprintf(stderr, "[wrap_rpc2] Original function found at %p\n", (void*)rpcmanager::original_register_booter);
    } else {
        fprintf(stderr, "[wrap_rpc2] Warning: Could not find original function\n");
    }

    fprintf(stderr, "[wrap_rpc2] RPC disabled via LD_PRELOAD\n");
}