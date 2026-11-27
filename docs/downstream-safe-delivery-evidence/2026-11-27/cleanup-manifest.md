# 娓呯悊娓呭崟锛圡07锛夆€?2026-11-27

鑼冨洿浠呴檺 `downstream-safe-delivery` 浠诲姟鍒涘缓/浣跨敤鐨勫璞°€?*涓嶅睘浜庢竻鐞嗚寖鍥?*锛?涓讳粨搴?`output/`銆乣baselines/`銆佺敤鎴锋湭璺熻釜鏂囨。銆乣.tmp/s14a`銆乣aiVideo-s14base`銆?涓讳粨搴?`.tmp/phase2b_*` 鍙婂叾浠栧巻鍙茶瘯楠岀洰褰曘€?
| # | 缁濆璺緞 | 鐢ㄩ€?| 鍙啀鐢?| 鍒犻櫎鍓嶄繚鐣欒瘉鎹?| 鍒犻櫎鏉′欢 |
|---|---|---|---|---|---|
| 1 | `<worktree>/.tmp/fresh_pytest` | 鏃╂湡 pytest 涓存椂鏍?| 鏄?| 鏃犲敮涓€璇佹嵁 | 鐩稿叧杩涚▼缁撴潫 |
| 2 | `<worktree>/.tmp/s07-clean` | 鏃у共鍑€鍓湰 | 鏄紙`git archive`锛?| 璇绘暟宸茶鍏ヨ处鏈?| 鎻愪氦鍥哄畾鍚庡彲閲嶅缓 |
| 3 | `<worktree>/.tmp/s07-debug` | S07 鎵嬪伐璋冭瘯绔犺妭 | 鏄?| 璋冭瘯缁撹宸茶鍏ヨ处鏈?璇佹嵁 | 鏃犳湭褰掓。澶辫触璇佹嵁 |
| 4 | `<worktree>/.tmp/s04-real` | 鐪熷疄绗?1+2 绔犲彧璇诲壇鏈笌瀹¤鑴氭湰 | 澶嶅埗浠讹紙鏉ユ簮锛氫富浠撳簱 `output/閿﹁。澶滆/...`锛?| 瀹¤璇绘暟宸茶鍏ヨ处鏈?| 纭鍘熷鏉ユ簮浠嶅湪涓讳粨搴?`output/` |
| 5 | `<worktree>/.tmp/head.zip` / `verify.zip` / `m06_untracked.json` | 婧愮爜蹇収涓庡熀绾胯褰?| 鏄?| `baseline.json` 宸插綊妗?| 鎻愪氦鍥哄畾鍚庡彲閲嶅缓 |
| 6 | `<worktree>/.pytest_tmp`銆乣.pytest_cache`銆乣.ruff_cache`銆乣**/__pycache__` | 娴嬭瘯/闈欐€佹鏌ョ紦瀛?| 鏄?| 鏃犻渶 | 杩涚▼缁撴潫 |
| 7 | `<worktree>/output` | 1 涓枃浠讹細`閿﹁。澶滆/part_01_鏉€闈掑窞/chapter_001/step02_events.json`锛堟棭鏈熸祴璇曟硠婕忓埌榛樿 OUTPUT_BASE 鐨勪骇鐗╋紝闈炴寮忔暟鎹級 | 鏄?| 宸茶褰曡矾寰?澶у皬锛涢潪姝ｅ紡浜х墿 | 纭鏃犳湭褰掓。鍞竴璇佹嵁 |
| 8 | `<worktree/>`锛堟暣涓?worktree锛?| 鏈换鍔″紑鍙?worktree | 鍚︼紙鎻愪氦鍦?master 鍚庝笉闇€瑕侊級 | 婧愮爜宸插湪 master锛涜瘉鎹湪 `docs/downstream-safe-delivery-evidence/2026-11-27/` | M05 鍚堝苟瀹屾垚銆佹棤鏈彁浜や慨鏀?杩涚▼ |
| 9 | 鍒嗘敮 `codex/downstream-safe-delivery` | 鏈换鍔″垎鏀?| 鍚?| 鏈€缁堟彁浜ゅ凡鍦?master | `git branch -d` 鎴愬姛 |
| 10 | `C:/myFloders/git/aiVideo/.tmp/downstream-safe-delivery-verify-482676b` | 鍥哄畾鎻愪氦骞插噣鍓湰 | 鏄紙`git archive`锛?| 85 passed 璇绘暟宸插綊妗?| M05 鍚堝苟瀹屾垚 |

鍒犻櫎鎵ц鏂瑰紡锛歅owerShell 鍘熺敓鍛戒护 + `-LiteralPath`锛屽垹闄ゅ墠瑙ｆ瀽缁濆璺緞骞剁‘璁や綅浜庣櫧鍚嶅崟锛?閫掑綊鍓嶆鏌ラ噸瑙ｆ瀽鐐?绗﹀彿閾炬帴锛涗笉璺熼殢鑼冨洿澶栭摼鎺ワ紱涓嶄娇鐢?`cmd /c`锛涗笉浣跨敤 `--force`銆?
**淇濈暀**锛氳瘉鎹洰褰曪紙闅?master 淇濈暀锛夈€佷富浠撳簱鐢ㄦ埛鏂囦欢涓庢寮忎骇鐗┿€佸叾浠栧伐浣滃尯銆?
## 鎵ц缁撴灉锛?026-11-27锛孧07锛?
| 瀵硅薄 | 缁撴灉 |
|---|---|
| `.tmp/fresh_pytest`銆乣.tmp/s07-clean`銆乣.tmp/s07-debug`銆乣.tmp/s04-real`銆乣.tmp/head.zip`銆乣.tmp/verify.zip` | 宸插垹闄わ紙s04-real 鐨勫師濮嬬珷鑺備粛鍦ㄤ富浠撳簱 `output/閿﹁。澶滆/...`锛屽凡鏍稿瀛樺湪锛?|
| `.pytest_tmp`銆乣.pytest_cache`銆乣.ruff_cache`銆佸悇绾?`__pycache__` | 宸插垹闄?|
| worktree `output/`锛? 涓祴璇曟硠婕忔枃浠?`step02_events.json`锛?| 宸插垹闄わ紙闈炴寮忎骇鐗╋級 |
| 鏈换鍔?worktree `C:/myFloders/git/aiVideo/.tmp/worktrees/downstream-safe-delivery` | `git worktree remove` 鎴愬姛锛堟湭鐢?`--force`锛夛紱`.tmp/worktrees/` 绌虹洰褰曚竴骞剁Щ闄?|
| 鍒嗘敮 `codex/downstream-safe-delivery` | `git branch -d` 鎴愬姛锛坢arker: 宸插悎骞讹級 |
| `C:/myFloders/git/aiVideo/.tmp/downstream-safe-delivery-verify-482676b` | 宸插垹闄?|
| 淇濈暀 | 涓讳粨搴?`output/`锛?191 鏂囦欢 / 989,104,390 瀛楄妭锛屾湭鍙橈級銆? 椤圭敤鎴锋湭璺熻釜鏂囦欢锛圫HA 鏈彉锛夈€乣.tmp/s14a`銆乣aiVideo-s14base`銆佹湰璇佹嵁鐩綍銆佷富浠撳簱鍏朵粬鍘嗗彶 `.tmp/phase2b_*` |

鍒犻櫎鍓嶉€愰」鏍￠獙浜嗙粷瀵硅矾寰勪綅浜庢湰浠诲姟 worktree 鍐呫€侀潪绗﹀彿閾炬帴/junction锛涙湭浣跨敤 `cmd /c`銆?鏈€掑綊鍒犻櫎涓讳粨搴?`.tmp` 鏁翠綋銆佹湭瀵瑰惈鐢ㄦ埛宸ヤ綔鐨勪粨搴撴墽琛?`reset`銆?