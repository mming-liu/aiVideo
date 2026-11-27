# 涓嬫父瀹夊叏鍑虹墖鍖咃紙S01鈥揝07锛夎瘉鎹綊妗?鈥?2026-11-27

鏈洰褰曚綅浜庝换鍔?worktree 涔嬪锛坄docs/` 宸查殢 fast-forward 鍚堝叆 master锛夛紝淇濆瓨鍙鏍哥殑鏈€灏忚瘉鎹€?**浠诲姟 worktree銆侀獙璇佸壇鏈€佺紦瀛樹笌鍙啀鐢熶复鏃舵枃浠跺凡浜?2026-11-27 娓呯悊瀹屾瘯**锛岃
`cleanup-manifest.md`锛涙湰鐩綍涓庝富浠撳簱 `output/`锛堟湭鏀瑰姩锛夋槸鍞竴淇濈暀璇佹嵁銆?
## 1. 浜や粯韬唤

| 椤圭洰 | 鍊?|
|---|---|
| 棣栨楠屾敹浠ｇ爜鎻愪氦 | `482676b`锛坄M01-M03: recoverable publish batch, audited duration identity, subtitle required checks`锛?|
| 鍚堝苟鎻愪氦 | `c55cc4c`锛坉ocs/璇佹嵁 + `.gitignore`锛沗482676b..c55cc4c` 鏃犵敓浜ф簮鐮佸彉鍖栵級 |
| M01 宕╂簝/骞跺彂淇鎻愪氦 | `631e269`锛坄fix(step04): persist publish intent before backup, verify recovery digests and hold one lock across recover/publish/cleanup`锛?|
| 鍚堝苟鐩爣 | master锛堥娆″悎骞跺墠 HEAD `507ccea`锛涘綋鍓嶅熀绾?`631e269`锛?|
| 鍒嗘敮 | `codex/downstream-safe-delivery`锛堝凡鍚堝苟骞跺垹闄わ級 |
| 鍚堝苟鏂瑰紡 | `git merge --ff-only` |

## 2. 鏈疆鏁存敼锛圡01鈥揗03锛?
- **M01 鍙戝竷鎵规**锛氭柊澧?`step04_merge/publication.py`銆傛垚鐗囥€佸簲浜や粯 SRT銆佽川閲忔姤鍛婂厛鍏ㄩ儴鍑嗗锛?  鍐嶆寜"澶囦唤鏃у€?鈫?鏇挎崲 鈫?璇诲洖 鈫?澶辫触鍥炴粴"鍙戝竷锛沯ournal 璁板綍杩涘害锛屾仮澶嶅け璐ユ姤
  `publish_unconfirmed` 骞朵繚鐣欐潗鏂欙紱鍚岀洰鏍囩敤 `AttemptLock` 涓茶鍖栵紱涓嬫杩愯鍏堟仮澶嶅啀寮€濮嬨€?  鏃х殑"鍙戝竷鍚庡崟鐙啓鎶ュ憡銆佸け璐ュ彧鍛婅"宸茬Щ闄ゃ€?- **M02 瀹¤韬唤**锛歚confirmed` 鍗曞厓蹇呴』鍚屾椂鏈夐潪绌?`audio_path` + `audio_sha256`锛屽惁鍒欐暣浠藉璁?  鎷掔敤骞剁粰鍑?`audit_identity_missing`锛沗prepared` 涓嶈础鐚疄娴嬩笅闄愶紱鍧?JSON / 闈炲璞?/ 閿?schema
  鍒嗗埆杩斿洖 `audit_unreadable` / `audit_not_object` / `audit_schema_mismatch`銆?- **M03 瀛楀箷蹇呮**锛氭湁瀛楀箷鍚堝悓鍗虫妸 `subtitle_duration` 璁″叆蹇呮锛涚己澶?涓嶅彲璇?鏈В鏋?鈬?  `UNVERIFIED`锛屾槑纭秺鐣?鈬?`BLOCK`锛涙棤瀛楀箷鍚堝悓涓嶆柊澧炶姹傦紱姹囨€绘寜"蹇呮椤规槸鍚︿骇鍑虹粨鏋?鍒ゅ畾銆?
### 2.1 M01 宕╂簝绐楀彛涓庡苟鍙戞仮澶嶏紙绗簩杞鏍稿悗锛宍631e269`锛?
- **鍏堝啓鎰忓浘鍐嶇Щ鍔?*锛歫ournal 鍦ㄥ浠界Щ鍔?*涔嬪墠**璁板綍 `backup_pending` + 澶囦唤璺緞 + 鏃ф憳瑕侊紱
  宕╂簝钀藉湪"宸茬Щ鍔ㄣ€佹湭鍐欑姸鎬?鐨勭獥鍙ｆ椂锛屾仮澶嶄粛鑳藉嚟 journal 鎵惧埌澶囦唤銆?- **鎭㈠鏍￠獙鎽樿涓庣粓鎬?*锛氬浠界己澶?鎽樿涓嶇/鐩爣鐗堟湰涓嶆槑 鈬?`publish_unconfirmed` 鎴?  `publish_conflict`锛屼繚鐣?journal 涓庡浠斤紝涓嶅啀瀹ｅ竷 `recovered`锛涘彧鏈夊叏閮ㄦ潯鐩‘瀹氬綊浣嶆墠鍒?journal銆?- **鍗曚竴鐩爣閿?*锛氫富鍏ュ彛鍙栧緱鍙戝竷閿佸悗鎶婇攣瀵硅薄浼犵粰 `recover_pending` 涓?`publish_batch`锛?  鎭㈠銆佹棫鐘舵€佽鍙栥€佸彂甯冦€佽鍥炵‘璁ゃ€佸浠戒笌 journal 娓呯悊閮藉湪鍚屼竴閿佸唴銆傜浜屼釜杩涚▼寰楀埌
  `publish_busy`锛屼笉浼氭妸鍒汉姝ｅ湪鍙戝竷鐨?journal 褰撴畫鐣欐仮澶嶃€?- 鏂板宕╂簝鍒囩偣涓庡苟鍙戞祴璇曪細`STEP04_PUBLISH_FAULT=crash` 鍦?澶囦唤瀹屾垚銆佺姸鎬佹湭钀界洏"澶勯€€鍑?99锛?  涓嬩竴娆¤繍琛屽厛鎭㈠鍐嶅彂甯冿紱鍙︽寔閿佽繘绋嬩娇涓诲叆鍙ｅけ璐ヤ笖鏃т骇鐗╀笉鍙樸€?
## 3. 绂荤嚎楠屾敹璇绘暟锛? 娆＄湡瀹炴ā鍨嬭皟鐢級

| 鑼冨洿 | 鍛戒护瑕佺偣 | 璇绘暟 |
|---|---|---|
| 鍙楀奖鍝嶅叏閲忥紙棣栬疆锛?| `pytest tests/tts tests/step02_video_gen tests/step02b_video_gen tests/step03_tts_audio tests/step04_merge tests/integration/test_s01_isolation_baseline.py tests/integration/test_s05_duration_audit.py tests/integration/test_downstream_safe_delivery_s07.py` | **313 passed / 6 skipped** |
| 鍚堝苟浜ゅ弶锛堥杞級 | 涓婅堪 + `tests/step02a_frame_gen` | **501 passed / 10 skipped** |
| 骞插噣鍓湰锛坄git archive 482676b`锛?| 鐩稿叧 10 浠芥祴璇?| **85 passed** |
| M01 淇鍚?step04+integration | `pytest tests/step04_merge tests/integration/test_s05_duration_audit.py tests/integration/test_downstream_safe_delivery_s07.py` | **117 passed / 2 skipped** |
| M01 淇鍚庡悎骞朵氦鍙夛紙涓讳粨搴擄紝`output/` 鍦ㄥ満锛?| `pytest tests/step02a_frame_gen tests/step02_video_gen tests/step02b_video_gen tests/step03_tts_audio tests/step04_merge tests/tts` + S01/S05/S07 | **626 passed / 10 skipped** |
| 骞插噣鍓湰锛坄git archive 631e269`锛屾棤 `.env.keys`/`output/`锛?| 鍙戝竷/璐ㄩ噺/韬唤/绛栫暐/鏍风墖鐩稿叧 7 浠芥祴璇?| **62 passed** |

skip 鍧囦负缂哄皯姝ｅ紡 `output/` 绔犺妭浜х墿鐨勬棦鏈夌敤渚嬶紝宸查€愰」鏍稿鍘熷洜锛涙柊澧炵敤渚嬫棤 skip/xfail銆?
鍏抽敭鏁呴殰鐢ㄤ緥锛堝潎缁忕湡瀹炲叆鍙?/ 鐙珛涓存椂鏍癸級锛?
- `STEP04_PUBLISH_FAULT=srt|quality`锛氬洖婊氬悗鏃ф垚鐗囥€佹棫 SRT銆佹棫璐ㄩ噺鎶ュ憡鎽樿涓嶅彉锛?  journal=`rolled_back`锛?- `STEP04_PUBLISH_FAULT=quality,rollback`锛歚publish_unconfirmed`锛屽浠戒笌 journal 淇濈暀锛?  鍘绘帀鏁呴殰閲嶈窇鏃跺厛鎭㈠鍐嶅彂甯冿紝鏈€缁?`PASS` 涓?journal 娓呯悊锛?- 缂栫爜鍚庣粓妫€ BLOCK锛坄MERGE_QUALITY_EXPECT_VIDEO_CODEC=h265`锛夛細鏃т氦浠樼墿涓嶅彉锛?- 瀹¤韬唤锛氱己璺緞/鎽樿銆佹憳瑕佷笉绗︺€佸潖 JSON銆侀敊 schema 鍧囨嫆鏀朵笖涓嶄骇鐢熻秴绐楄鍐筹紱
  `prepared` 甯?`actual` 涔熶笉璐＄尞瀹炴祴涓嬮檺锛?- 瀛楀箷锛氱己澶?涓嶅彲瑙ｆ瀽 鈬?`UNVERIFIED`锛涜秺鐣?鈬?`BLOCK`锛涙棤瀛楀箷鍚堝悓 鈬?`PASS`銆?
## 4. 鐢熸晥閰嶇疆

瑙?`config.json`銆傚叏閮ㄤ负榛樿鍊硷細`VOICE_TIER_POLICY=strict`銆?`VIDEO_MEDIA_ENFORCE=0`銆乣MERGE_QUALITY_ENFORCE=0`銆?`MERGE_AUDIO_OVERFLOW_POLICY=fail`銆傛樉寮?`tiered` / `enforce=1` 鐨勮兘鍔涗粛鐢辨棦鏈夋祴璇曡鐩栵紝
鏈负閫氳繃榛樿娴嬭瘯鑰屽垹闄ゃ€?
## 5. 淇濇姢娓呭崟

- 涓讳粨搴撴湭璺熻釜鏂囦欢 7 椤癸紙鏂囦欢锛夌殑璺緞涓?SHA-256 瑙?`baseline.json`锛屾湰杞鏍稿悗閲嶆柊鍝堝笇涓€鑷达紱
  绗?8 椤?`baselines/2026-11-12-pre-layered/s06_ab/tools/__pycache__/` 鏄洰褰曪紝鍘熷熀绾垮彧璁拌矾寰勶紝
  鐜版寜鍐呴儴 1 涓枃浠剁殑鎽樿璁板綍鍦?`output-baseline.json`銆?- 涓讳粨搴撴寮?`output/`锛?191 涓枃浠?/ 989,104,390 瀛楄妭锛岃嚜 M06 璧锋暟閲忎笌鎬诲瓧鑺傛湭鍙橈紱
  **閫愭枃浠?SHA-256 娓呭崟**鍦ㄤ粨搴撳
  `C:\myFloders\git\aiVideo\.archive\output-baseline-2026-11-27\sha256.txt`
  锛堟竻鍗曟枃浠?SHA-256 `a63b8255鈥7f4b9`锛岃 `output-baseline.json`锛夈€?  璇存槑锛歁06 褰撴椂鍙湁鏁伴噺/鎬诲瓧鑺傦紝**涓嶈兘鍥炴函璇佹槑鍘嗗彶鍐呭鏈彉**锛涢€愭枃浠舵竻鍗曟槸鏈琛ュ厖鐨?  鍓嶅悜鍩虹嚎锛屽彲璇佹槑姝ゅ悗鏈彉銆?- 鏈Е纰?`.tmp/s14a`銆乣aiVideo-s14base`銆?
## 5.1 鏍风墖閲嶅缓锛堟柊楠岃瘉锛岄潪鍘熻繍琛屼骇鐗╋級

棣栨鍚堝苟鍓嶇殑 S07 鏍风墖宸查殢 worktree 娓呯悊鍒犻櫎锛屽綊妗ｄ腑娌℃湁瀹炰綋濯掍綋銆傛湰杞粠鍥哄畾鎻愪氦
`631e269` 闅旂閲嶅缓锛? 妯″瀷璋冪敤锛夛紝淇濆瓨鍦ㄤ粨搴撳锛?
`C:\myFloders\git\aiVideo\.archive\downstream-s07-rebuild-631e269\`

鍖呭惈 `final.mp4`锛?0.0s锛?,240,172 瀛楄妭锛孲HA-256 `139c53da鈥?9e91d`锛夈€乣subtitles.srt`銆?`step04_quality.json`銆乣step04_media_quality.json`銆乣step03_duration_audit.json`銆?`voice_state.json`銆乣step02b_quality.json` 涓?`manifest.json`銆?`manifest.json` 鏄庣‘鏍囨敞"鏂伴獙璇侊紝闈?2026-11-27 鍘熻繍琛屼骇鐗?锛屽苟璁板綍鐢熸垚鎻愪氦銆佺敓鎴愬櫒涓庢ā鍨嬭皟鐢ㄦ暟=0銆?
## 6. 鏈墽琛?
鐪熷疄 Step02b/Step03 浠樿垂璋冪敤銆佹寮忓獟浣撻噰绾炽€佺敓浜х珷鑺傞噸璺戙€佽繙绔帹閫併€丼tage 3/4銆?