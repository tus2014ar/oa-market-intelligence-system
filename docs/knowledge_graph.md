# The knowledge graph (DL-87)

The facts the project already holds, as nodes and named relations, so a question such as "who makes the competing injectables?" or "what happened around the peak?" can be answered with each fact's source beside it. It adds **no analysis**: every node and edge comes from a table or reference file already in the repository.

It is a committed file, `data/reference/knowledge_graph.json` (311 nodes, 827 edges), built by `src/oa_market_intelligence/knowledge_graph.py`. Pure Python; `networkx` is imported only by `Graph.to_networkx()`.

```bash
oa-graph                 # or: python -m oa_market_intelligence.knowledge_graph
```

## Nodes

| Type | Count | From |
|---|---|---|
| product | 160 | `dim_product` (brand or generic tag, and the earliest FDA approval where the openFDA file has one) |
| manufacturer | 74 | `dim_product` |
| drug class | 6 | `dim_product.treatment_category` |
| competitor group | 4 | `data/reference/competitor_groups.csv` |
| specialty | 50 | `dim_specialty` |
| disease area | 2 | `dim_product.disease_area` (OA or RA, the IQVIA extract's area, not a label indication) |
| event | 14 | `data/reference/events.csv` (date, type, source, verified flag) |
| market | 1 | the IQVIA NMTA visit market itself |

## Relations (the only ones allowed)

| Relation | From, to | Evidence |
|---|---|---|
| `made_by` | product, manufacturer | `dim_product` |
| `in_class` | product, drug class | `dim_product` |
| `in_disease_area` | product, disease area | `dim_product` |
| `in_group` | product, competitor group | `competitor_groups.csv`, with the rule that placed it |
| `compared_with` | Zilretta group, each injectable-steroid group | `competitor_groups.csv` |
| `prescribed_by` | competitor group, specialty | `fact_product_visits`: visits summed over all months, and the specialty's share of the group's visits |
| `involves` | event, product, manufacturer or disease area | `data/reference/event_links.csv`: only where the event's own text names the entity |
| `occurred_in` | event, market | `events.csv`: the event falls in the period |

Every edge carries an `evidence` field naming its source, and the file lists those sources.

## What it does not claim
- **No causal relation exists.** An event is never linked to a change in share. The March to July 2024 dip is in the graph as an event with `verified = 0`, linked to nothing.
- **`compared_with` is the project's frozen grouping**, written before any group-level number was looked at (DL-73). It is a documented choice, not a statement about the market.
- **Visits are product-visits** inside IQVIA's panel: a patient visit can count under more than one product, so a group's visits are not a count of patients. The panel-coverage caveat applies.
- **Disease area is the extract's area**, not the label indication.
- Products with no manufacturer in `dim_product` have no `made_by` edge; none is invented.

## Keeping it right
`tests/test_knowledge_graph.py` builds small graphs by hand (every rule, every failure) and then checks the committed file against the committed warehouse and reference files: it equals a fresh build byte for byte, every edge has known ends and a named source, no node is unconnected, there are 160 products each with one class, area and group, the visit weights add up to the warehouse total and each group's shares add to one, and Zilretta's maker, approval date and visits equal the warehouse. The build is deterministic.

When a new extract arrives and `oa-publish` has run, run `oa-graph` and commit the result; the test fails until you do, with that instruction in its message.

## Where it is used
- **Claude's question box** (DL-88): four tools, `get_entity`, `get_neighbors`, `get_competitors` and `get_events`, read the graph; a fifth, `show_chart`, can draw "where Zilretta's visits come from" from its weights. Every answer that uses the graph lists its evidence files as a source. See [site.md](site.md).
- **The interactive Market map** on the site follows in the next change.
