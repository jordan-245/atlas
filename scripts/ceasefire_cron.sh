#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Ceasefire Probability Tracker — hourly news evaluation
#
# 1. Gets current factor state (Python)
# 2. Prints factor keywords for agent context (Python)
# 3. Spawns pi agent to search news and update factor toggles
# 4. Refreshes dashboard
#
# Cron: hourly (0 * * * *)
# Cost: ~$0.05-0.10 per run (sonnet)
# ═══════════════════════════════════════════════════════════════
set -uo pipefail

PROJECT="/root/atlas"
LOG_DIR="$PROJECT/logs"
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
LOG_FILE="$LOG_DIR/ceasefire-tracker_${TIMESTAMP}.log"

export TZ="Australia/Brisbane"
export HOME="${HOME:-/root}"
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

# Export API keys directly — sourcing .profile under set -u fails because
# .bashrc references $PS1 (unbound in cron), causing instant script death.
export BRAVE_API_KEY="${BRAVE_API_KEY:-BSAHxsnvVgTqZewUgDPpVSMP8SFmJB2}"

mkdir -p "$LOG_DIR"
cd "$PROJECT"

echo "=== Ceasefire Tracker — $TIMESTAMP ===" > "$LOG_FILE"

# ── Step 1: Get current factor state ──
echo "[$(date '+%H:%M:%S')] Getting factor state..." >> "$LOG_FILE"
python3 scripts/ceasefire_tracker.py status >> "$LOG_FILE" 2>&1
echo "[$(date '+%H:%M:%S')] Factor state captured." >> "$LOG_FILE"

# ── Step 2: Get factor keywords + state for agent context ──
echo "[$(date '+%H:%M:%S')] Generating factor evaluation context..." >> "$LOG_FILE"
FACTOR_FILE="/tmp/ceasefire_factors_${TIMESTAMP}.txt"
python3 scripts/ceasefire_tracker.py evaluate > "$FACTOR_FILE" 2>>"$LOG_FILE"
if [ ! -s "$FACTOR_FILE" ]; then
    echo "ERROR: ceasefire_tracker.py evaluate returned empty" >> "$LOG_FILE"
    rm -f "$FACTOR_FILE"
    exit 1
fi
echo "[$(date '+%H:%M:%S')] Factor context generated ($(wc -c < "$FACTOR_FILE") bytes)" >> "$LOG_FILE"

# ── Step 3: Spawn pi agent to search news and update toggles ──
echo "[$(date '+%H:%M:%S')] Spawning evaluation agent..." >> "$LOG_FILE"

# shellcheck disable=SC2034
read -r -d '' PROMPT << 'AGENTPROMPT'
You are the Atlas Ceasefire Probability Tracker agent. Every hour you assess
geopolitical developments and update the Iran/US ceasefire factor model.

## YOUR DATA FILES
1. **Factor state + keywords**: /tmp/ceasefire_factors_TS.txt
   This lists all 26 factors with their current TRUE/FALSE state and search keywords.

Read this file FIRST using the read tool before taking any action.

## YOUR TASK

For EACH of the 26 factors in the file:

1. Use the **brave-search skill** to search for the factor's keywords.
   The brave-search skill is at: /root/.pi/agent/skills/pi-skills/brave-search/SKILL.md
   Read the skill first, then execute searches.
   Run: `node /root/.pi/agent/skills/pi-skills/brave-search/search.js "KEYWORDS" -n 10 --freshness pd`

2. Evaluate whether the factor's condition is currently TRUE or FALSE based on:
   - Recent news from the past 24 hours (look for 🔴 RECENT section)
   - Focus on Reuters, AP, Bloomberg, BBC for reliable sourcing
   - Ignore pundit speculation, think-tank opinion pieces

3. If the factor state CHANGED (from TRUE to FALSE or vice versa), run:
   ```bash
   cd /root/atlas && python3 scripts/ceasefire_tracker.py toggle FACTOR_ID true|false --confidence high|medium|low --source "Source Name Date"
   ```

4. After checking ALL factors, run:
   ```bash
   cd /root/atlas && python3 scripts/ceasefire_tracker.py recalculate
   ```

## SOURCE QUALITY RULES
- **Reuters, AP, Bloomberg, BBC named correspondent** = high confidence
- **Government/official statement** = high confidence
- **Multiple corroborating sources** = medium confidence
- **Single outlet, unconfirmed** = medium confidence
- **Pundit, think-tank, speculation** = DO NOT toggle based on this alone

## IMPORTANT RULES
- Only toggle a factor if you have solid news evidence from the past 24 hours
- Do NOT re-toggle based on old news that was already captured in the current state
- When uncertain, keep the existing state — "if in doubt, don't toggle"
- For ceasefire factors (weight positive): toggle TRUE when evidence is strong
- For escalation factors (weight negative): toggle TRUE when the escalation is ongoing

## FACTOR EVALUATION GUIDELINES

**DIPLOMATIC factors** (supreme_leader, backchannel, trump_tone, war_powers_house, coalition_fracture, un_resolution):
- These require CONCRETE ACTIONS, not rhetoric
- "Oman offering talks" ≠ backchannel TRUE — both sides must be confirmed participating
- Trump saying "we want peace eventually" ≠ trump_tone TRUE — needs specific deal/off-ramp language
- UN resolution only TRUE if actually PASSED with binding language

**MILITARY factors** (iran_collapse, navy_destroyed, ground_troops, kurdish_front, hezbollah_major, houthi_red_sea, us_casualties_spike, nato_engaged):
- iran_collapse: TRUE if Iranian missile/drone capability confirmed degraded >50%
- hezbollah_major: TRUE if Hezbollah launching significant (50+ rocket) barrages
- houthi_red_sea: TRUE if Houthis actively attacking ships in Red Sea
- us_casualties_spike: TRUE if US military deaths in single day >10

**POLITICAL factors** (war_powers_dead, gas_prices, maga_split, poll_collapse, iran_regime_falls, assassination_personal):
- war_powers_dead: TRUE if Senate voted to kill War Powers Resolution (already TRUE from March 4)
- gas_prices: TRUE if US national average >$5/gallon (already TRUE)
- assassination_personal: TRUE if there is active assassination cycle/retaliation threat (already TRUE)

**ECONOMIC factors** (oil_100, hormuz_escort, insurance_restored, qatar_restart, recession_fears, storage_limits):
- oil_100: TRUE only if Brent SUSTAINED above $100 for multiple days
- insurance_restored: TRUE if Lloyd's/major insurers RESUME normal-rate war risk cover
- hormuz_escort: TRUE if US Navy has established FORMAL convoy escort program

## SEND TELEGRAM BRIEFING
After recalculate, check if EITHER:
1. Probability changed by 3+ points from previous value, OR
2. Any factor flipped state (TRUE↔FALSE)

If EITHER condition is met, send a Telegram briefing:
```python
import sys; sys.path.insert(0, '/root/atlas')
from utils.telegram import send_message
send_message("""☮️ <b>Ceasefire Tracker [HH:MM AEST]</b>

<b>Probability: XX% (LABEL)</b>
<b>Timeline:</b> XX
<b>Action:</b> XX

<b>Changes this hour:</b>
• factor_id: FALSE → TRUE (reason + SOURCE)

<b>Active factors: X/26</b>
  📈 Ceasefire: factor1 (+15), factor2 (+8) ...
  📉 Escalation: factor3 (-12), factor4 (-8) ...

<b>Thesis:</b> Hold/Monitor/Exit summary""")
```

If NO changes: skip Telegram — just recalculate silently.

## TOOL ORDER
1. Read factor file (read tool)
2. For each factor: search news (bash: node search.js)
3. For any changes: toggle (bash: python3 ceasefire_tracker.py toggle ...)
4. Recalculate (bash: python3 ceasefire_tracker.py recalculate)
5. Send Telegram if changes (python in bash)
AGENTPROMPT

# Substitute timestamp in file paths
PROMPT="${PROMPT//TS/$TIMESTAMP}"

timeout 600 pi -p --no-session --model anthropic/claude-sonnet-4-6 "$PROMPT" >> "$LOG_FILE" 2>&1
PI_EXIT=$?

echo "[$(date '+%H:%M:%S')] Agent exit: $PI_EXIT" >> "$LOG_FILE"

# ── Step 4: Refresh dashboard ──
echo "[$(date '+%H:%M:%S')] Refreshing dashboard..." >> "$LOG_FILE"
python3 dashboard/generate_data.py >> "$LOG_FILE" 2>&1 || true

# ── Cleanup ──
rm -f "$FACTOR_FILE"
find "$LOG_DIR" -name "ceasefire-tracker_*.log" -mtime +7 -delete 2>/dev/null

echo "[$(date '+%H:%M:%S')] Done" >> "$LOG_FILE"
exit 0
