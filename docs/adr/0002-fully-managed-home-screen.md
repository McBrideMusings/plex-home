# Fully-managed home screen

The config is the complete source of truth for the pinned home screen. On every run the script resolves all slots, pins the resolved collections, and unpins anything currently pinned that is not in the resolved set. The script owns the entire pinned home screen — there is no concept of "user-pinned" hubs that the script leaves alone.

If something should be pinned, it belongs in the config. Manual pins made outside the config will be removed on the next run.
