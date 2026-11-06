import common
import status_tab

ctx = common.context()
status_tab.render(ctx.D["run"], ctx.STATUS, common.data_chip)
