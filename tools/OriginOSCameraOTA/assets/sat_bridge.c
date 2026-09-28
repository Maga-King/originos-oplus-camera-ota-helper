/*
 * OriginOS-preserving OPlus CameraService transaction bridge.
 *
 * The OriginOS cameraserver imports BnCameraService::onTransact from
 * libcamera_client.so. The experimental cameraserver changes that one NEEDED
 * entry to this library. Every normal framework transaction is forwarded to
 * an exact private copy of the OriginOS libcamera_client. Only OPlus' private
 * transaction range is offered to libcsextimpl first.
 *
 * This avoids replacing CameraService itself and therefore preserves the
 * OriginOS ICameraService Binder/Parcel ABI.
 */

typedef unsigned int uint32_t;
typedef unsigned long size_t;
typedef int status_t;

enum {
    RTLD_NOW_LOCAL = 2,
    ANDROID_LOG_INFO = 4,
    ANDROID_LOG_ERROR = 6,
    OPLUS_FIRST_TRANSACTION = 10001,
    OPLUS_LAST_TRANSACTION = 10027,
};

typedef status_t (*OnTransactFn)(void*, uint32_t, const void*, void*, uint32_t);
typedef void* (*GetFactoryFn)(void);
typedef void* (*GetObjectFn)(void);

extern void* dlopen(const char* filename, int flags);
extern void* dlsym(void* handle, const char* symbol);
extern const char* dlerror(void);
extern int __android_log_print(int priority, const char* tag, const char* format, ...);

static const char kTag[] = "OplusSatBridge";
static const char kPackageTag[] = "OplusPkgBridge";

/*
 * The OriginOS cameraserver cannot use crDroid's CameraService::
 * getCurrPackageName implementation because CameraService is linked into the
 * executable and its Binder ABI must remain OriginOS-native.  Remember callers
 * that actually use OPlus' private transaction range instead.  The patched
 * configureStreamsLocked code asks oplus_sat_is_calling_client() before it
 * adds com.oplus.packageName to the session parameters.
 */
extern void* ipc_thread_state_self(void)
        __asm__("_ZN7android14IPCThreadState4selfEv");
extern int ipc_thread_state_get_calling_uid(const void*)
        __asm__("_ZNK7android14IPCThreadState13getCallingUidEv");

enum { MAX_OPLUS_CLIENT_UIDS = 8 };
static int gOplusClientUids[MAX_OPLUS_CLIENT_UIDS] = {
        -1, -1, -1, -1, -1, -1, -1, -1,
};

static int get_calling_uid(void) {
    void* state = ipc_thread_state_self();
    if (state == (void*)0) return -1;
    return ipc_thread_state_get_calling_uid(state);
}

/*
 * CameraService has already resolved and validated AttributionSource.packageName
 * when the cameraserver hook calls oplus_pkg_register_client().  Keep that real
 * package name associated with the Binder caller instead of globally pretending
 * that every camera client is com.oplus.camera.
 *
 * No libc string functions are used here so the interposer keeps the same tiny
 * dependency surface as the already-tested bridge.
 */
enum {
    MAX_CAMERA_CLIENTS = 16,
    MAX_CAMERA_PACKAGE_NAME = 128,
    AID_SYSTEM = 1000,
    AID_CAMERASERVER = 1047,
};

typedef struct {
    int uid;
    unsigned int generation;
    char packageName[MAX_CAMERA_PACKAGE_NAME];
} CameraClientPackage;

typedef struct {
    const char* data;
    size_t count;
} OplusPackageView;

static CameraClientPackage gCameraClients[MAX_CAMERA_CLIENTS];
static int gCameraClientsInitialized;
static int gCameraClientsLock;
static unsigned int gCameraClientsGeneration;
static int gLastCameraClientSlot = -1;

static void package_lock(void) {
    while (__atomic_exchange_n(&gCameraClientsLock, 1, __ATOMIC_ACQUIRE)) {
    }
}

static void package_unlock(void) {
    __atomic_store_n(&gCameraClientsLock, 0, __ATOMIC_RELEASE);
}

static size_t package_copy(char* destination, const char* source) {
    size_t length = 0;
    if (source != (const char*)0) {
        while (length + 1 < MAX_CAMERA_PACKAGE_NAME && source[length] != '\0') {
            destination[length] = source[length];
            ++length;
        }
    }
    destination[length] = '\0';
    return length;
}

static size_t package_length(const char* value) {
    size_t length = 0;
    while (length + 1 < MAX_CAMERA_PACKAGE_NAME && value[length] != '\0') {
        ++length;
    }
    return length;
}

static int package_equal(const char* left, const char* right) {
    size_t i = 0;
    for (;;) {
        if (left[i] != right[i]) return 0;
        if (left[i] == '\0') return 1;
        ++i;
    }
}

static void initialize_camera_clients_locked(void) {
    if (gCameraClientsInitialized) return;
    for (int i = 0; i < MAX_CAMERA_CLIENTS; ++i) {
        gCameraClients[i].uid = -1;
        gCameraClients[i].generation = 0;
        gCameraClients[i].packageName[0] = '\0';
    }
    gCameraClientsInitialized = 1;
}

/* Called from the two CameraService::connectHelper instantiations. */
__attribute__((visibility("default")))
void oplus_pkg_register_client(const void* packageStringObject) {
    const unsigned char* object = (const unsigned char*)packageStringObject;
    const char* packageName = (const char*)0;
    if (object != (const unsigned char*)0) {
        /* Android libc++ string: short data at +1, long data pointer at +16. */
        packageName = (object[0] & 1)
                ? *(const char* const*)(object + 16)
                : (const char*)(object + 1);
    }
    int uid = get_calling_uid();
    if (uid < 0 || packageName == (const char*)0 || packageName[0] == '\0') {
        return;
    }

    package_lock();
    initialize_camera_clients_locked();

    int slot = -1;
    int oldestSlot = 0;
    unsigned int oldestGeneration = gCameraClients[0].generation;
    for (int i = 0; i < MAX_CAMERA_CLIENTS; ++i) {
        if (gCameraClients[i].uid == uid) {
            slot = i;
            break;
        }
        if (slot < 0 && gCameraClients[i].uid < 0) slot = i;
        if (gCameraClients[i].generation < oldestGeneration) {
            oldestSlot = i;
            oldestGeneration = gCameraClients[i].generation;
        }
    }
    if (slot < 0) slot = oldestSlot;

    gCameraClients[slot].uid = uid;
    gCameraClients[slot].generation = ++gCameraClientsGeneration;
    package_copy(gCameraClients[slot].packageName, packageName);
    gLastCameraClientSlot = slot;
    package_unlock();
}

/* Called once per configureStreams session by the cameraserver code cave. */
__attribute__((visibility("default")))
OplusPackageView oplus_pkg_get_current(void) {
    OplusPackageView result = { (const char*)0, 0 };
    int uid = get_calling_uid();
    int usedServiceFallback = 0;

    package_lock();
    initialize_camera_clients_locked();
    int slot = -1;
    for (int i = 0; i < MAX_CAMERA_CLIENTS; ++i) {
        if (gCameraClients[i].uid == uid && gCameraClients[i].packageName[0]) {
            slot = i;
            break;
        }
    }
    /* Match ColorOS' last-connected global only for service-internal reconfig. */
    if (slot < 0 && (uid == AID_CAMERASERVER || uid == AID_SYSTEM) &&
            gLastCameraClientSlot >= 0) {
        slot = gLastCameraClientSlot;
        usedServiceFallback = 1;
    }
    if (slot >= 0) {
        result.data = gCameraClients[slot].packageName;
        result.count = package_length(gCameraClients[slot].packageName) + 1;
    }
    package_unlock();

    if (result.data == (const char*)0 || result.count <= 1) {
        __android_log_print(ANDROID_LOG_INFO, kPackageTag,
                "[UNKNOWN] uid=%d package=<none> action=skip-injection", uid);
    } else if (package_equal(result.data, "com.oplus.camera")) {
        __android_log_print(ANDROID_LOG_INFO, kPackageTag,
                "[OPLUS] OnePlus Camera uid=%d package=%s action=inject-real-package%s",
                uid, result.data, usedServiceFallback ? " fallback=service" : "");
    } else {
        __android_log_print(ANDROID_LOG_INFO, kPackageTag,
                "[THIRD_PARTY] Camera client uid=%d package=%s action=inject-real-package%s",
                uid, result.data, usedServiceFallback ? " fallback=service" : "");
    }
    return result;
}

static void remember_oplus_client(void) {
    int uid = get_calling_uid();
    if (uid < 0) return;

    for (int i = 0; i < MAX_OPLUS_CLIENT_UIDS; ++i) {
        int saved = __atomic_load_n(&gOplusClientUids[i], __ATOMIC_ACQUIRE);
        if (saved == uid) return;
        if (saved == -1) {
            int empty = -1;
            if (__atomic_compare_exchange_n(&gOplusClientUids[i], &empty, uid,
                    0, __ATOMIC_RELEASE, __ATOMIC_RELAXED)) {
                __android_log_print(ANDROID_LOG_INFO, kTag,
                        "registered OPlus private camera client uid=%d", uid);
                return;
            }
        }
    }
}

__attribute__((visibility("default")))
int oplus_sat_is_calling_client(void) {
    int uid = get_calling_uid();
    if (uid < 0) return 0;
    for (int i = 0; i < MAX_OPLUS_CLIENT_UIDS; ++i) {
        if (__atomic_load_n(&gOplusClientUids[i], __ATOMIC_ACQUIRE) == uid) {
            return 1;
        }
    }
    return 0;
}

__attribute__((visibility("default")))
status_t sat_bridge_on_transact(
        void* self, uint32_t code, const void* data, void* reply, uint32_t flags)
        __asm__("_ZN7android8hardware15BnCameraService10onTransactEjRKNS_6ParcelEPS2_j");

/* Already loaded as our DT_NEEDED child; use its SONAME in every namespace. */
static const char kOriginalClient[] = "libcamera_ori.so";
static const char kExtension[] = "/system_ext/lib64/libcsextimpl.so";
static const char kOriginalSymbol[] =
        "_ZN7android8hardware15BnCameraService10onTransactEjRKNS_6ParcelEPS2_j";
static const char kExtensionSymbol[] =
        "_ZN7android20CameraServiceExtImpl10onTransactEjRKNS_6ParcelEPS1_j";

static OnTransactFn gOriginalOnTransact;
static OnTransactFn gExtensionOnTransact;
static void* gExtensionObject;
static int gOriginalInitDone;
static int gExtensionInitDone;

static void load_original(void) {
    if (gOriginalInitDone) return;
    gOriginalInitDone = 1;

    void* handle = dlopen(kOriginalClient, RTLD_NOW_LOCAL);
    if (handle == (void*)0) {
        __android_log_print(ANDROID_LOG_ERROR, kTag,
                "dlopen original client failed: %s", dlerror());
        return;
    }
    gOriginalOnTransact = (OnTransactFn)dlsym(handle, kOriginalSymbol);
    if (gOriginalOnTransact == (OnTransactFn)0) {
        __android_log_print(ANDROID_LOG_ERROR, kTag,
                "dlsym original onTransact failed: %s", dlerror());
        return;
    }
    if (gOriginalOnTransact == (OnTransactFn)sat_bridge_on_transact) {
        gOriginalOnTransact = (OnTransactFn)0;
        __android_log_print(ANDROID_LOG_ERROR, kTag,
                "original lookup resolved back to the bridge; refusing recursion");
        return;
    }
    __android_log_print(ANDROID_LOG_INFO, kTag,
            "OriginOS BnCameraService forwarding is ready");
}

static void load_extension(void) {
    if (gExtensionInitDone) return;
    gExtensionInitDone = 1;

    void* handle = dlopen(kExtension, RTLD_NOW_LOCAL);
    if (handle == (void*)0) {
        __android_log_print(ANDROID_LOG_ERROR, kTag,
                "dlopen OPlus extension failed: %s", dlerror());
        return;
    }

    GetFactoryFn getFactory = (GetFactoryFn)dlsym(handle, "getExtFactoryImpl");
    gExtensionOnTransact = (OnTransactFn)dlsym(handle, kExtensionSymbol);
    if (getFactory == (GetFactoryFn)0 ||
            gExtensionOnTransact == (OnTransactFn)0) {
        __android_log_print(ANDROID_LOG_ERROR, kTag,
                "OPlus extension symbols are incomplete: %s", dlerror());
        gExtensionOnTransact = (OnTransactFn)0;
        return;
    }

    /* Proprietary factory ABI: pointer -> pointer -> factory function. */
    void* first = getFactory();
    if (first == (void*)0) return;
    void* second = *(void**)first;
    if (second == (void*)0) return;
    GetObjectFn makeObject = *(GetObjectFn*)second;
    if (makeObject == (GetObjectFn)0) return;
    gExtensionObject = makeObject();
    if (gExtensionObject == (void*)0) {
        gExtensionOnTransact = (OnTransactFn)0;
        __android_log_print(ANDROID_LOG_ERROR, kTag,
                "OPlus extension factory returned null");
        return;
    }

    __android_log_print(ANDROID_LOG_INFO, kTag,
            "OPlus private transaction bridge is ready");
}

__attribute__((constructor))
static void initialize_bridge(void) {
    /* Resolve the stock target before Binder traffic can arrive. */
    load_original();
}

status_t sat_bridge_on_transact(
        void* self, uint32_t code, const void* data, void* reply, uint32_t flags) {
    load_original();

    if (code >= OPLUS_FIRST_TRANSACTION && code <= OPLUS_LAST_TRANSACTION) {
        /* This happens before the first OPlus configureStreams call. */
        remember_oplus_client();
        load_extension();
        if (gExtensionOnTransact != (OnTransactFn)0 &&
                gExtensionObject != (void*)0) {
            status_t extensionStatus = gExtensionOnTransact(
                    gExtensionObject, code, data, reply, flags);
            __android_log_print(ANDROID_LOG_INFO, kTag,
                    "OPlus transaction code=%u status=%d flags=%u",
                    code, extensionStatus, flags);
            if (extensionStatus == 0) {
                return 0;
            }
        } else {
            __android_log_print(ANDROID_LOG_ERROR, kTag,
                    "OPlus transaction code=%u has no extension target", code);
        }
    }

    if (gOriginalOnTransact != (OnTransactFn)0) {
        return gOriginalOnTransact(self, code, data, reply, flags);
    }

    /* NAME_NOT_FOUND: fail closed instead of recursing into this interposer. */
    return -2;
}
