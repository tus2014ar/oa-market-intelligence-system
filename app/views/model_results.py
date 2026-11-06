import common
import model_results_tab

ctx = common.context()
model_results_tab.render(ctx.D, ctx.ADOPTION, ctx.POSTHOC, ctx.ROUND2, common.data_chip)
