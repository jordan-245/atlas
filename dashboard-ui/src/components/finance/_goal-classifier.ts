/**
 * _goal-classifier.ts -- conservative classifier for "is this saver-type
 * account a long-term goal pot (Travel, Emergency, Savings, Invest, House
 * deposit, etc.) or a fortnight budget bucket (Rent, Food, Phone, Fuel, Gym,
 * Bills, Spending, Fun, AI, ...)?"
 *
 * Up Bank's API returns every named saver bucket under `type: "saver"`, so
 * the dashboard can't distinguish goals from budget buckets server-side.
 * The user manages both shapes alongside each other (e.g. `🚨 Emergency` and
 * `🏡 Rent` are both `saver`). The Finance tab's goal panel should only
 * surface true goals.
 *
 * Strategy (deliberately conservative):
 *   1. Strip emoji/symbols and tokenise the account name on whitespace +
 *      punctuation.
 *   2. If ANY token matches a budget keyword, classify as budget → exclude.
 *   3. If ANY token matches a goal keyword, classify as goal → include.
 *   4. Otherwise (unknown name): include only when the user has explicitly
 *      stored a target dollar value on it (signals goal intent). Without
 *      that signal we exclude — better to under-show goals than to surface a
 *      mis-labelled budget bucket as a goal.
 *
 * Budget tokens win over goal tokens by design — names like "Car insurance"
 * resolve to budget (the `insurance` token forces exclusion) even though
 * `car` is a goal token. This matches the user's "be conservative" guidance.
 *
 * Keep this file dependency-free so it can be unit tested in isolation.
 */

// Tokens that mark an account as a fortnight budget bucket — these are the
// kinds of recurring expenses the user funds from each pay, not goals to
// accumulate toward. Be liberal here: any match excludes the account.
const BUDGET_KEYWORDS: ReadonlySet<string> = new Set([
  // housing / utilities
  'rent', 'mortgage', 'lease',
  'electricity', 'electric', 'power', 'water', 'gas', 'internet', 'wifi',
  // comms / subscriptions
  'phone', 'mobile', 'sim', 'subscription', 'subscriptions', 'subs',
  // recurring lifestyle
  'gym', 'fitness',
  'fun', 'entertainment', 'hobby', 'hobbies', 'streaming',
  // personal spending
  'spending', 'spend', 'pocket', 'allowance',
  // transport (the budget side — fuel/rego/service/insurance)
  'fuel', 'petrol', 'diesel', 'transport', 'transit', 'parking', 'toll', 'tolls',
  'service', 'rego', 'registration', 'registeration',
  'insurance',
  // food & groceries
  'food', 'groceries', 'grocery', 'lunch', 'dinner', 'breakfast', 'coffee', 'alcohol',
  // misc bills / utilities catch-all
  'bill', 'bills', 'utility', 'utilities',
  // pets / health / personal
  'pet', 'pets', 'vet', 'health', 'medical', 'pharmacy', 'dentist',
  'beauty', 'clothes', 'clothing', 'haircut',
  // ai / tooling / other recurring buckets the user owns
  'ai',
])

// Tokens that mark an account as a goal — long-term savings or investment
// pot. Any match (in the absence of a budget token) includes the account.
const GOAL_KEYWORDS: ReadonlySet<string> = new Set([
  // travel
  'travel', 'holiday', 'holidays', 'vacation', 'trip', 'trips',
  // safety net
  'emergency', 'rainy', 'buffer',
  // generic savings / investing
  'savings', 'saver', 'save', 'nest',
  'invest', 'investing', 'investment', 'investments', 'portfolio',
  // big-ticket purchases
  'house', 'home', 'deposit', 'property',
  'car',
  'wedding', 'baby', 'kids',
  // long horizon
  'retirement', 'pension', 'super',
  // generic goal-shape names
  'fund', 'goal', 'goals', 'dream', 'dreams', 'future',
])

/**
 * Normalise an account name to lowercase ASCII tokens, dropping emoji,
 * punctuation and other non-alphanumeric characters.
 *
 * Exported for tests and reuse.
 */
export function tokenizeName(name: string | undefined | null): string[] {
  if (!name) return []
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
}

/**
 * Decide whether a saver-type account should be treated as a goal pot.
 *
 * @param name              The account display name as returned by Up Bank.
 * @param hasStoredTarget   True iff the user has set a per-account target $
 *                          via the dashboard (signals explicit goal intent).
 */
export function isGoalAccount(
  name: string | undefined | null,
  hasStoredTarget: boolean,
): boolean {
  const tokens = tokenizeName(name)
  if (tokens.length === 0) return false

  // Budget always wins.
  for (const t of tokens) {
    if (BUDGET_KEYWORDS.has(t)) return false
  }

  // Then look for an explicit goal token.
  for (const t of tokens) {
    if (GOAL_KEYWORDS.has(t)) return true
  }

  // Unknown name with no budget signal — only include if the user has
  // already attached a goal target. Conservative on purpose.
  return hasStoredTarget
}
