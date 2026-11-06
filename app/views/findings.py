import common
import findings_tab

ctx = common.context()
findings_tab.render(ctx.FINDINGS, common.data_chip)
