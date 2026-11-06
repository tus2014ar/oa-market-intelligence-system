import common
import market_trend

ctx = common.context()
market_trend.render(ctx.D, common.data_chip, common.filters())
