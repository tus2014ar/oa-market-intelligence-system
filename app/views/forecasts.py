import common
import forecasts_tab

ctx = common.context()
forecasts_tab.render(ctx.D, ctx.ADOPTION, common.data_chip, common.filters())
