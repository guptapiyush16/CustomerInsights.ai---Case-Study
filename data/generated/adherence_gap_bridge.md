# Adherence gap bridge

Methodology: a sequential decomposition in the order **population -> measure definition -> data source**.
The order is fixed so the effects sum exactly to the observed CRM-minus-pipeline gap; changing the order
would change individual effects because the factors can interact.

The stages are:

1. Pipeline observed: all enrolled patients, hub-fill PDC >= 80%.
2. Population-adjusted: patients with at least one specialty-pharmacy fill, still using hub-fill PDC >= 80%.
3. Definition-adjusted: the same fill population, using a hub-derived on-time refill flag.
4. CRM observed: the same fill population, using the case-manager flag; missing CRM notes remain in the denominator and count as not on schedule.

| Quarter | Pipeline | CRM | Gap | Population effect | Definition effect | Source effect | Bridge check |
|---|---:|---:|---:|---:|---:|---:|---:|
| this_quarter | 58.0% | 71.0% | +13.0% | +0.0% | +6.4% | +6.6% | +13.0% |
| last_quarter | 64.0% | 72.0% | +8.0% | +0.0% | +1.3% | +6.7% | +8.0% |

Effects are percentage-point contributions to CRM minus pipeline. A positive value means that factor
moves the result toward the CRM rate; a negative value widens the difference in the selected sequence.
