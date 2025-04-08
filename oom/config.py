# config.py

# Predefined patterns to extract specific memory info from log sections
patterns = {
    'active_anon': r'active_anon:(\d+)',
    'inactive_anon': r'inactive_anon:(\d+)',
    'isolated_anon': r'isolated_anon:(\d+)',
    'active_file': r'active_file:(\d+)',
    'inactive_file': r'inactive_file:(\d+)',
    'isolated_file': r'isolated_file:(\d+)',
    'unevictable': r'unevictable:(\d+)',
    'dirty': r'dirty:(\d+)',
    'writeback': r'writeback:(\d+)',
    'slab_reclaimable': r'slab_reclaimable:(\d+)',
    'slab_unreclaimable': r'slab_unreclaimable:(\d+)',
    'mapped': r'mapped:(\d+)',
    'shmem': r'shmem:(\d+)',
    'pagetables': r'pagetables:(\d+)',
    'bounce': r'bounce:(\d+)',
    'free': r'free:(\d+)',
    'free_pcp': r'free_pcp:(\d+)',
    'free_cma': r'free_cma:(\d+)',
    'pagecache': r'(\d+) total pagecache pages',
    'swapcache': r'(\d+) pages in swap cache',
    'reserved': r'(\d+) pages reserved',
    'total_pages_ram': r'(\d+) pages RAM',
    'free_swap': r'Free swap\s*=\s*(\d+)kB',
    'total_swap': r'Total swap\s*=\s*(\d+)kB',

    # Add patterns for hugepage-related information
    'hugepages_total': r'hugepages_total=(\d+)',       # Total hugepages
    'hugepages_free': r'hugepages_free=(\d+)',         # Free hugepages
    'hugepages_surp': r'hugepages_surp=(\d+)',         # Surplus hugepages
    'hugepages_size': r'hugepages_size=(\d+)kB',       # Size of hugepages in kB
}

# Regex to detect the Mem-Info line
mem_info_pattern = r'(.*Mem-Info.*)'

# Regex to detect the OOM invocation line
oom_pattern = r'(.* invoked oom-killer:.*)'
