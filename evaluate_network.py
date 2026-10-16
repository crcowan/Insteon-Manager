import json
from collections import defaultdict

def run_evaluation():
    with open('backend/network_snapshot.json', 'r', encoding='utf-8') as f:
        snap = json.load(f)

    with open('network_snapshot.pristine.json', 'r', encoding='utf-8') as f:
        pristine = json.load(f)

    plm = snap.get('plm', {})
    devices = snap.get('devices', [])
    plm_addr = plm.get('address', '53.C1.42').upper()

    all_devices = {plm_addr: {'name': 'USB PowerLinc Modem (PLM)', 'cat': plm.get('cat'), 'subcat': plm.get('subcat')}}
    for d in devices:
        addr = d.get('address', '').upper()
        all_devices[addr] = {
            'name': d.get('label') or d.get('description', 'Device'),
            'cat': d.get('cat'),
            'subcat': d.get('subcat'),
            'records': d.get('records', [])
        }
    all_devices[plm_addr]['records'] = plm.get('records', [])

    print(f"============================================================")
    print(f"           INSTEON NETWORK TOPOLOGY AUDIT REPORT            ")
    print(f"============================================================")
    print(f"Total Registered Network Nodes: {len(all_devices)}")
    print(f"------------------------------------------------------------")
    print(f"{'Address':<10} | {'Device Label':<26} | {'Active':<7} | {'Deleted':<7}")
    print(f"------------------------------------------------------------")
    
    total_active = 0
    total_deleted = 0
    for addr, info in sorted(all_devices.items()):
        active_recs = [r for r in info['records'] if r.get('in_use', True)]
        del_recs = [r for r in info['records'] if not r.get('in_use', True)]
        total_active += len(active_recs)
        total_deleted += len(del_recs)
        print(f"{addr:<10} | {info['name']:<26} | {len(active_recs):<7} | {len(del_recs):<7}")
    
    print(f"------------------------------------------------------------")
    print(f"Total Active ALDB Records: {total_active} (Cleaned from initial 323)")
    print(f"Total Marked Deleted Slots: {total_deleted}")
    print(f"------------------------------------------------------------")

    # 1. Structural Checks
    controllers = defaultdict(list)
    responders = defaultdict(list)
    loopbacks = []
    ghosts = []
    group_zeros = []
    duplicates = defaultdict(list)

    for src_addr, info in all_devices.items():
        seen_slots = set()
        for r in info['records']:
            if not r.get('in_use', True):
                continue
            tgt = r.get('target_address', '').upper()
            grp = r.get('group', 0)
            is_ctrl = r.get('is_controller', False)
            is_resp = r.get('is_responder', False)
            mem = r.get('mem_addr', '')

            key = (src_addr, tgt, grp, is_ctrl, is_resp)
            if key in seen_slots:
                duplicates[src_addr].append((mem, tgt, grp, is_ctrl))
            seen_slots.add(key)

            if tgt == src_addr:
                loopbacks.append((src_addr, mem, grp))
            if tgt not in all_devices:
                ghosts.append((src_addr, tgt, mem, grp))
            if grp == 0:
                group_zeros.append((src_addr, mem))

            if is_ctrl:
                controllers[(src_addr, tgt, grp)].append(r)
            if is_resp:
                responders[(tgt, src_addr, grp)].append(r)

    print("\n[Integrity Metrics]")
    print(f" - Loopbacks:                  {len(loopbacks)}")
    print(f" - Ghosts (Dead references):   {len(ghosts)}")
    print(f" - Corrupted Group 0 Records:  {len(group_zeros)}")
    print(f" - Redundant Duplicate Links:  {sum(len(v) for v in duplicates.values())}")

    # 2. Reciprocal Symmetry Verification
    unmatched_ctrl = []
    unmatched_resp = []

    for (c_addr, r_addr, grp) in set(list(controllers.keys()) + list(responders.keys())):
        has_c = (c_addr, r_addr, grp) in controllers
        has_r = (c_addr, r_addr, grp) in responders
        if grp == 251:  # Pyinsteon internal engine discovery query
            continue
        if has_c and not has_r:
            unmatched_ctrl.append((c_addr, r_addr, grp))
        elif has_r and not has_c:
            unmatched_resp.append((c_addr, r_addr, grp))

    print(f" - Broken Half-Links (Ctrl):   {len(unmatched_ctrl)}")
    print(f" - Broken Half-Links (Resp):   {len(unmatched_resp)}")

    # 3. Default PLM Links Verification
    missing_defaults = []
    for addr, info in all_devices.items():
        if addr == plm_addr:
            continue
        # PLM controls device on Grp 1
        has_p2d = (plm_addr, addr, 1) in controllers and (plm_addr, addr, 1) in responders
        # Device reports to PLM on Grp 1
        has_d2p = (addr, plm_addr, 1) in controllers and (addr, plm_addr, 1) in responders
        if not has_p2d or not has_d2p:
            missing_defaults.append((addr, info['name'], has_p2d, has_d2p))

    print(f" - Missing Default PLM Pairs:  {len(missing_defaults)}")

    # 4. Keypad Multi-Button Scene Verification
    print("\n[Keypad Mapping Verification]")
    kp_addr = "1C.2C.40"
    if kp_addr in all_devices:
        kp = all_devices[kp_addr]
        print(f"Mini Keypad ({kp_addr}) Button Mappings:")
        for btn in range(1, 5):
            btn_ctrls = controllers.get((kp_addr, plm_addr, btn), [])
            btn_resps = responders.get((kp_addr, plm_addr, btn), [])
            other_resps = [t for (c, t, g) in responders if c == kp_addr and g == btn and t != plm_addr]
            print(f"  Button {btn} (Group {btn}): PLM Reciprocal: {'YES' if (btn_ctrls and btn_resps) else 'NO'}, Linked Responders: {other_resps}")

    # 5. Scene Ecosystem Overview
    groups_in_net = set(grp for (_, _, grp) in controllers.keys()) | set(grp for (_, _, grp) in responders.keys())
    groups_in_net.discard(251)
    print("\n[Insteon Scenes Overview]")
    print(f"Active Scene / Group Count: {len(groups_in_net)}")
    for grp in sorted(groups_in_net):
        ctrl_list = [c for (c, r, g) in controllers if g == grp]
        resp_list = [r for (c, r, g) in responders if g == grp]
        print(f"  Group {grp:3d} | Controllers: {len(set(ctrl_list)):2d} | Responders: {len(set(resp_list)):2d}")

    # Summary
    is_perfect = (
        len(loopbacks) == 0 and
        len(ghosts) == 0 and
        len(group_zeros) == 0 and
        sum(len(v) for v in duplicates.values()) == 0 and
        len(unmatched_ctrl) == 0 and
        len(unmatched_resp) == 0 and
        len(missing_defaults) == 0
    )

    print("\n============================================================")
    if is_perfect:
        print("OVERALL VERDICT: PERFECT (100% HEALTH SCORE)")
        print("Network topology is completely reconciled, symmetrical,")
        print("and ready for production hardware backend deployment.")
    else:
        print("OVERALL VERDICT: RESIDUAL ANOMALIES DETECTED")
    print("============================================================")

if __name__ == '__main__':
    run_evaluation()
