"""Whitelist of config keys that the Web UI may read / write.

This is the FastAPI equivalent of the old ``Dashboard/static/js/settings_id_list.js``
file. The frontend receives this list via ``GET /api/config/schema`` so the Vue
client does not need to hard-code it.
"""

WEB_SETTINGS_KEYS: list[str] = [
    'bangumi_dir',
    'temp_dir',
    'classify_bangumi',
    'lock_resolution',
    'segment_download_mode',
    'add_bangumi_name_to_video_filename',
    'add_resolution_to_video_filename',
    'download_resolution',
    'default_download_mode',
    'check_frequency',
    'multi-thread',
    'multi_downloading_segment',
    'customized_video_filename_prefix',
    'customized_video_filename_suffix',
    'ua',
    'use_mobile_api',
    'danmu',
    'use_proxy',
    'proxy',
    'read_sn_list_when_checking_update',
    'read_config_when_checking_update',
    'save_logs',
    'quantity_of_logs',
    'download_cd',
    'parse_sn_cd',
    'parse_cd',
]
