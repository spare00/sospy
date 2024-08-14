# config.py

patterns = {
    'active_anon': r'active_anon:(\d+)',
    'inactive_anon': r'inactive_anon:(\d+)',
    'active_file': r'active_file:(\d+)',
    'inactive_file': r'inactive_file:(\d+)',
    'slab_reclaimable': r'slab_reclaimable:(\d+)',
    'slab_unreclaimable': r'slab_unreclaimable:(\d+)',
    'shmem': r'shmem:(\d+)',
    'pagetables': r'pagetables:(\d+)',
    'free': r'free:(\d+)',
    'free_pcp': r'free_pcp:(\d+)',
    'pagecache': r'(\d+) total pagecache pages',
    'reserved': r'(\d+) pages reserved',
    'total_pages_ram': r'(\d+) pages RAM',
}

mem_info_pattern = r'(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}).*Mem-Info:'
oom_pattern = r'(.* invoked oom-killer:.*)'
