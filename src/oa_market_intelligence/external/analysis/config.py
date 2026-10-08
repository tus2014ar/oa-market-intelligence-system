"""Every threshold and setting of the external-data analyses, fixed by the protocol (DL-59).

A test pins each value to the protocol text. Changing one is a deviation, logged in the decision
log, never a tuning step.
"""

SEED = 0
N_BOOT = 2000
CI_LEVEL = 0.95

# E1: does Medicare's specialty ranking agree with IQVIA's?
E1_YEARS = (2020, 2024)
E1_FIRST_MONTH, E1_LAST_MONTH = 202001, 202412
E1_RHO_MIN = 0.6
E1_TOP_K = 3
E1_TOP_MIN = 2
IQVIA_65_PLUS = ("65 TO 74", "75 TO 84", "85 +")

# E2: does the Medicare and company-sales picture match the IQVIA turn?
E2A_PEAK_YEARS = (2021, 2022)
E2A_LAST_YEAR = 2024
E2B_AGREEMENT_MIN = 0.70
E2B_FLAT_BAND = 0.01  # a change under 1 percent either way counts as flat

# E3: state adoption
E3_MIN_PROVIDERS = 30
E3_RANK_RHO_MIN = 0.7
E3_RANK_YEARS = (2022, 2024)

# E4: hypotheses about the 2022 turn
H1_DECLINE = -0.25
H1_WINDOW = (202011, 202207)
H1_MAX_LAG = 6
H1_ALPHA = 0.05
H1_BLOCK = 6
H1_N_PERM = 2000
H2_CHANGE = 0.10
H2_FROM, H2_TO = "2020Q2", "2021Q2"
H3_WINDOW_MONTHS = 6
H3_ANCHOR_MONTH = 202104  # the month after the end of pass-through status (31 March 2021)
H4_DROP = 0.10

# details the protocol text implies
H2_J3301_MG_PER_UNIT = 10  # J3301 is quoted per 10 mg, J3304 per 1 mg
H4_MONTHS = (3, 7)  # March to July
H4_YEAR, H4_BASE_YEAR = 2024, 2023
E2B_QUARTERS = ("2020Q3", "2025Q2")

# Gap diagnosis (protocol section 9): why do company sales and IQVIA visits diverge?
GAP_BASE_YEAR, GAP_LAST_YEAR = 2021, 2024
GAP_ALT_BASE_YEAR = 2022  # sensitivity: the IQVIA peak instead of the Medicare peak
D1_WINDOW = 0.15  # IQVIA 65+ within 15 points of Medicare; under-65 at least 15 points lower
D2_SHIFT = 0.10  # facility share of services up by 10 points
D3_SALES, D3_MEDICARE = 0.25, 0.10  # sales per visit up 25%, a Medicare measure up 10%
D4_TOP2 = 0.50  # the two biggest groups account for half of the fall
# sensitivity grid, primary value in the middle
D1_GRID = (0.10, 0.15, 0.20)
D2_GRID = (0.05, 0.10, 0.15)
D3_GRID = ((0.15, 0.05), (0.25, 0.10), (0.35, 0.15))
D4_GRID = (0.40, 0.50, 0.60)
