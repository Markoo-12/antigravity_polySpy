# antigravity_polySpy
Scoring Formula V2.0
TotalScore = (Σ Feature × Weight) × CoordinationFactor
Feature Weights
Feature	Base Points	Weight	Description
Bridge Funding	+40	1.0	Funded via cross-chain bridge
Win Rate	+30	1.0	>75% win rate on $5k+ volume
Quiet Accumulation	+30	1.0	Large trade in low-volume market
New Wallet	0-50	1.0	Linear decay: 50 - (Days × 7)
Low Activity	+20	1.0	<10 total transactions
Maduro Rule	+30	1.0	Single-market concentration
Round Number	+15	1.0	Trade is multiple of $1,000
Coordination Factor
1.0× — No cluster detected
1.5× — Sybil cluster detected (≥3 wallets within 10min window)
Sample Calculations
Scenario A: Coordinated Sybil Attack
IMPORTANT

3 fresh wallets buy the same outcome token within 5 minutes, each with $20,000 round trades.

Wallet 1: 0xAAA...
Feature	Points	Note
New Wallet (0.5 days)	47	50 - (0.5 × 7) = 47
Low Activity (3 txns)	20	
Single-Market	30	Only position
Round Number ($20k)	15	Multiple of 1000
Base Score	112	
× Coordination Factor	1.5	3 wallets in cluster
Final Score	168	🚨 High Alert
Wallets 2 & 3: Similar scores (150-170 each)
Total Cluster Impact: $60,000 with avg score ~160

Scenario B: Lone Whale
Single established wallet makes a $50,000 bet on a market.

Feature	Points	Note
Bridge Funding	40	Funded from Across 2h ago
Win Rate (82%)	30	High historical accuracy
New Wallet	0	Wallet is 45 days old
Low Activity	0	150+ transactions
Single-Market	0	Diversified positions
Round Number ($50k)	15	Multiple of 1000
Base Score	85	
× Coordination Factor	1.0	No cluster
Final Score	85	⚠️ Alert (≥70)
Verification Results
✓ Wallet analyzer unit tests PASSED
  - check_round_number(5000) == True
  - check_round_number(5001) == False
  - calculate_wallet_age_score(0) == 50
  - calculate_wallet_age_score(3) == 29
  - calculate_wallet_age_score(8) == 0
✓ Module imports PASSED
  - InsiderScorer, detect_coordination, validate_signals
  - InsiderScoreResult, CoordinationResult, ValidationResult
✓ Feature weights config PASSED
  - All 7 features configured with weight 1.0
