"""Stdlib-only response validation, embedded into the sealed Diana20 clients."""


def validate_native_scale_response(result, pins):
    if type(result) is not dict:
        return False
    scale, update = result.get('card_scale'), result.get('recognizer_update')
    if type(scale) is not dict or type(update) is not dict:
        return False
    runtime = update.get('runtime')
    if type(runtime) is not dict or runtime.get('card_scale') != scale:
        return False
    if (scale.get('adapter_version') != 'diana20-pixel-native-scale-v1'
            or scale.get('template_transform') != 'NONE_NATIVE_ONLY'
            or type(scale.get('matcher_attempts')) is not int
            or scale['matcher_attempts'] not in (0, 1)
            or any(type(value) is not list for value in
                   (result.get('cards'), result.get('unknown'), update.get('effective_cards'), update.get('effective_unknown')))
            or type(update.get('additional_machine_candidate_count')) is not int
            or not 0 <= update['additional_machine_candidate_count'] <= 52
            or any(value is not False for value in
                   (result.get('canonical_allowed'), result.get('complete_deal'), update.get('canonical_allowed'),
                    update.get('complete_deal'), update.get('human_verified'), runtime.get('geometry_enabled')))):
        return False
    if scale['matcher_attempts'] == 0 and (result['cards'] or update['effective_cards']
                                         or update['additional_machine_candidate_count'] != 0):
        return False
    status = scale.get('selection_status')
    if status in ('UNKNOWN', 'TIE'):
        return (scale.get('selected_variant') is None and scale.get('selected_sprite_sha256') is None
                and scale.get('native_size') is None and result.get('sprite_sha256') is None
                and runtime.get('sprite_sha256') is None and runtime.get('margin_applied') is False
                and scale['matcher_attempts'] == 0 and result['cards'] == []
                and update['effective_cards'] == [] and update.get('additional_machine_candidate_count') == 0)
    variant = scale.get('selected_variant')
    if status != 'SELECTED' or type(variant) is not int or variant not in pins:
        return False
    digest, width, height = pins[variant]
    if (scale.get('status') != 'MEASURED' or scale.get('width_state') != 'measured'
            or scale.get('height_state') != 'measured' or scale.get('native_size') != [width, height]
            or scale.get('selected_sprite_sha256') != digest or result.get('sprite_sha256') != digest
            or runtime.get('sprite_sha256') != digest or runtime.get('margin_applied') is not (variant == 5)):
        return False
    for value, native in ((scale.get('width_px'), width), (scale.get('height_px'), height)):
        if type(value) not in (int, float) or not native - 1 <= value <= native + 1:
            return False
    if variant != 5 and (update['additional_machine_candidate_count'] != 0
                         or update['effective_cards'] != result['cards']
                         or update['effective_unknown'] != result['unknown']):
        return False
    return True
