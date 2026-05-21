# YAML for config format

The config file is YAML. The config schema involves nested objects, arrays of mixed-shape items (fixed slots vs. pick slots, groups with varying constraint combinations), and inline comments for documentation — all of which YAML handles naturally. TOML was considered but becomes awkward with arrays of heterogeneous objects.
