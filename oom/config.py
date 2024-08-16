# config.py

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
    'reserved': r'(\d+) pages reserved',
    'total_pages_ram': r'(\d+) pages RAM',
}

mem_info_pattern = r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}).*Mem-Info:'
oom_pattern = r'(.* invoked oom-killer:.*)'
