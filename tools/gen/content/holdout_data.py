"""Двухтабличные и временные data-задачи с точными контрактами."""
from tools.gen.holdout_types import Case


def cases():
    out = []
    for qa, qb, answer in [(3, 2, 43), (5, 1, 46)]:
        out.append(Case("data_analysis", "version_join",
                        "Join orders.csv with prices.csv by sku. Use the price with largest version per sku. "
                        "What is total quantity times latest price?", str(answer), "3×7+2×11=43; 5×7+11=46.",
                        {"orders.csv": f"sku,quantity\na,{qa}\nb,{qb}\n",
                         "prices.csv": "sku,version,price\na,1,5\nb,1,11\na,2,7\n"}))
    for lo, hi, answer in [(10, 20, 10), (12, 25, 11)]:
        out.append(Case("data_analysis", "interval_overlap",
                        f"sessions.csv contains half-open intervals [start,end) in integer minutes. "
                        f"Sum each session's overlap duration with [{lo},{hi}); count simultaneous sessions separately.",
                        str(answer), "[10,20): 2+8+0=10; [12,25): 0+6+5=11.",
                        {"sessions.csv": "start,end\n0,12\n8,18\n20,30\n"}, oracle=dict(lo=lo, hi=hi)))
    for rows, answer in [("10,2\n20,3\n99,0\n", 16), ("5,1\n9,2\n100,0\n", 7)]:
        out.append(Case("data_analysis", "weighted_floor",
                        "measurements.csv has value,weight. Compute floor(sum(value*weight)/sum(weight)); "
                        "zero weights contribute nothing.", str(answer), "80/5=16; floor(23/3)=7.",
                        {"measurements.csv": "value,weight\n" + rows}))
    for values, answer in [([5, 9, 4, 6, 2], 17), ([4, 7, 5, 8, 1], 16)]:
        rows = "key,time,units\n" + "".join(f"{key},{time},{value}\n" for (key, time), value in
                                             zip([("a", 1), ("a", 3), ("b", 2), ("b", 4), ("c", 1)], values))
        out.append(Case("data_analysis", "latest_event",
                        "In events.csv keep the row with the greatest time per key, then sum units. "
                        "Times within a key are unique.", str(answer), "Последние a,b,c: 9+6+2=17; 7+8+1=16.",
                        {"events.csv": rows}))
    out += [Case("data_analysis", "missing_price",
                 "Compute quantity times price for sku z using orders.csv and prices.csv. No other data is available.",
                 None, "Цена z отсутствует; её нельзя вывести из цены a.",
                 {"orders.csv": "sku,quantity\nz,2\n", "prices.csv": "sku,price\na,5\n"}),
            Case("data_analysis", "conflicting_latest",
                 "Use the highest version per sku in prices.csv. What is the latest price of a? "
                 "No tie rule or other data is available.", None, "Две разные цены одной максимальной версии без приоритета.",
                 {"prices.csv": "sku,version,price\na,2,7\na,2,9\n"})]
    return out
