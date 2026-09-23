/*
 * hwbench native kernels - compiled on demand by hwbench.executors.native.
 *
 * Why a C file in the repo: memory latency, FP peak and multi-core scaling
 * cannot be measured from Python (interpreter overhead dominates), and the
 * project deliberately does not shell out to third-party benchmark binaries.
 * Every kernel is self-contained; the only dependency is libc + libm.
 *
 * Contract: one JSON object on stdout, exit code 0 on success.
 *   {"mode": "...", "value": <double>, "unit": "...", "seconds": <double>, ...}
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#if defined(_WIN32)
#include <windows.h>
static double now_sec(void) {
    LARGE_INTEGER f, c;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&c);
    return (double)c.QuadPart / (double)f.QuadPart;
}
#else
static double now_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}
#endif

static volatile double g_sink_d;
static volatile unsigned long long g_sink_u;

/* ---- FP64 FMA throughput ------------------------------------------------ */
static void kernel_flops(double seconds_target) {
    const int unroll = 16;
    double a[unroll], b = 1.0000001, c = 0.9999999;
    for (int i = 0; i < unroll; i++) a[i] = 1.0 + i * 1e-9;
    double t0 = now_sec();
    unsigned long long iters = 0;
    for (;;) {
        for (int r = 0; r < 4096; r++) {
            for (int i = 0; i < unroll; i++) a[i] = a[i] * c + b;
        }
        iters++;
        if ((iters & 15) == 0 && now_sec() - t0 >= seconds_target) break;
    }
    double elapsed = now_sec() - t0;
    double acc = 0;
    for (int i = 0; i < unroll; i++) acc += a[i];
    g_sink_d = acc;
    /* one FMA = 2 FLOPs */
    double flops = (double)iters * 4096.0 * unroll * 2.0;
    printf("{\"mode\":\"flops\",\"value\":%.3f,\"unit\":\"GFLOPS\",\"seconds\":%.4f,"
           "\"note\":\"single-thread dependent FMA chain\"}\n",
           flops / elapsed / 1e9, elapsed);
}

/* ---- 64-bit integer throughput ------------------------------------------ */
static void kernel_intops(double seconds_target) {
    unsigned long long x = 0x243F6A8885A308D3ULL;
    double t0 = now_sec();
    unsigned long long iters = 0;
    for (;;) {
        for (int r = 0; r < 100000; r++) {
            x = x * 6364136223846793005ULL + 1442695040888963407ULL;
        }
        iters++;
        if ((iters & 7) == 0 && now_sec() - t0 >= seconds_target) break;
    }
    double elapsed = now_sec() - t0;
    g_sink_u = x;
    double ops = (double)iters * 100000.0 * 2.0; /* multiply + add, dependent chain */
    printf("{\"mode\":\"intops\",\"value\":%.3f,\"unit\":\"GOPS\",\"seconds\":%.4f,"
           "\"note\":\"dependent 64-bit LCG chain, 2 ops per step\"}\n",
           ops / elapsed / 1e9, elapsed);
}

/* ---- memory: STREAM-like triad / copy ---------------------------------- */
static void kernel_triad(size_t mb) {
    size_t n = mb * 1024 * 1024 / sizeof(double);
    double *a = (double *)malloc(n * sizeof(double));
    double *b = (double *)malloc(n * sizeof(double));
    double *c = (double *)malloc(n * sizeof(double));
    if (!a || !b || !c) { fprintf(stderr, "alloc failed\n"); exit(2); }
    for (size_t i = 0; i < n; i++) { b[i] = 1.0 + i * 1e-9; c[i] = 2.0; a[i] = 0.0; }
    const double s = 3.0;
    /* warm-up pass: fault in every page so the timed pass measures DRAM, not page faults */
    for (size_t i = 0; i < n; i++) a[i] = b[i] + s * c[i];
    double t0 = now_sec();
    for (size_t i = 0; i < n; i++) a[i] = b[i] + s * c[i];
    double elapsed = now_sec() - t0;
    g_sink_d = a[n / 2];
    printf("{\"mode\":\"triad\",\"value\":%.3f,\"unit\":\"GB/s\",\"seconds\":%.4f,"
           "\"region_mb\":%zu,\"note\":\"a=b+s*c, 24 bytes moved per element\"}\n",
           (double)n * 24.0 / elapsed / 1e9, elapsed, mb);
    free(a); free(b); free(c);
}

static void kernel_copy(size_t mb) {
    size_t n = mb * 1024 * 1024 / sizeof(double);
    double *a = (double *)malloc(n * sizeof(double));
    double *b = (double *)malloc(n * sizeof(double));
    if (!a || !b) { fprintf(stderr, "alloc failed\n"); exit(2); }
    for (size_t i = 0; i < n; i++) { b[i] = 1.0 + i * 1e-9; a[i] = 0.0; }
    memcpy(a, b, n * sizeof(double)); /* warm-up: fault in destination pages */
    double t0 = now_sec();
    memcpy(a, b, n * sizeof(double));
    double elapsed = now_sec() - t0;
    g_sink_d = a[n / 2];
    printf("{\"mode\":\"copy\",\"value\":%.3f,\"unit\":\"GB/s\",\"seconds\":%.4f,"
           "\"region_mb\":%zu,\"note\":\"memcpy, read+write traffic\"}\n",
           (double)n * 16.0 / elapsed / 1e9, elapsed, mb);
    free(a); free(b);
}

/* ---- memory latency: random pointer chase ------------------------------- */
static void kernel_latency(size_t kb, long steps) {
    size_t n = kb * 1024 / sizeof(size_t);
    if (n < 16) n = 16;
    size_t *idx = (size_t *)malloc(n * sizeof(size_t));
    size_t *perm = (size_t *)malloc(n * sizeof(size_t));
    if (!idx || !perm) { fprintf(stderr, "alloc failed\n"); exit(2); }
    for (size_t i = 0; i < n; i++) perm[i] = i;
    unsigned long long rng = 0x9E3779B97F4A7C15ULL;
    for (size_t i = n - 1; i > 0; i--) {
        rng = rng * 6364136223846793005ULL + 1442695040888963407ULL;
        size_t j = (size_t)((rng >> 33) % (i + 1));
        size_t tmp = perm[i]; perm[i] = perm[j]; perm[j] = tmp;
    }
    /* chain: idx[perm[i]] = perm[i+1] (cyclic) so the chase is pointer-chasing */
    for (size_t i = 0; i < n; i++) idx[perm[i]] = perm[(i + 1) % n];
    size_t p = 0;
    for (size_t i = 0; i < n; i++) p = idx[p]; /* warm the chain */
    double t0 = now_sec();
    for (long i = 0; i < steps; i++) p = idx[p];
    double elapsed = now_sec() - t0;
    g_sink_u = (unsigned long long)p;
    printf("{\"mode\":\"latency\",\"value\":%.3f,\"unit\":\"ns\",\"seconds\":%.4f,"
           "\"region_kb\":%zu,\"steps\":%ld,\"note\":\"dependent random pointer chase\"}\n",
           elapsed / (double)steps * 1e9, elapsed, kb, steps);
    free(idx); free(perm);
}

/* ---- clock estimate is intentionally absent -----------------------------
 * A dependent add chain gets folded away by -O2 (we measured a fake 37 THz),
 * and a volatile accumulator measures store-forwarding instead of the clock.
 * Base/max clock comes from the device inventory instead.
 */

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: kernels <flops|intops|triad|copy|latency> [args]\n");
        return 64;
    }
    const char *mode = argv[1];
    if (strcmp(mode, "flops") == 0) {
        kernel_flops(argc > 2 ? atof(argv[2]) : 0.5);
    } else if (strcmp(mode, "intops") == 0) {
        kernel_intops(argc > 2 ? atof(argv[2]) : 0.5);
    } else if (strcmp(mode, "triad") == 0) {
        kernel_triad(argc > 2 ? (size_t)atoi(argv[2]) : 256);
    } else if (strcmp(mode, "copy") == 0) {
        kernel_copy(argc > 2 ? (size_t)atoi(argv[2]) : 256);
    } else if (strcmp(mode, "latency") == 0) {
        kernel_latency(argc > 2 ? (size_t)atoi(argv[2]) : 8192, argc > 3 ? atol(argv[3]) : 2000000);
    } else {
        fprintf(stderr, "unknown mode: %s\n", mode);
        return 64;
    }
    return 0;
}
