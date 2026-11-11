"""Новые семейства math; фиксированные ответы с ручной проверкой."""
from tools.gen.holdout_types import Case


def cases():
    out = []
    for a, m, b, n, answer in [(2, 3, 3, 5, 8), (1, 4, 3, 7, 17)]:
        out.append(Case("math", "congruence",
                        f"Find the smallest nonnegative integer x with x mod {m} = {a} and x mod {n} = {b}.",
                        str(answer), f"Перебор остатков до {answer}: первый общий элемент {answer}.",
                        oracle=dict(a=a, m=m, b=b, n=n)))
    for w, h, answer in [(2, 2, 2), (3, 2, 4)]:
        out.append(Case("math", "blocked_paths",
                        f"Count paths from (0,0) to ({w},{h}), moving only (1,0) or (0,1). "
                        "The vertex (1,1) is forbidden.", str(answer),
                        "Комбинаторика: 6−2×2=2; 10−2×3=4.", oracle=dict(w=w, h=h)))
    for counts, answer in [([20, 18, 15, 7, 5, 4, 2], 39), ([12, 10, 9, 4, 3, 2, 1], 23)]:
        a, b, c, ab, ac, bc, abc = counts
        out.append(Case("math", "three_sets",
                        f"Three sets have sizes A={a}, B={b}, C={c}. Pair intersections (including the triple) "
                        f"have sizes AB={ab}, AC={ac}, BC={bc}; the triple has size {abc}. "
                        "How many elements are in their union?", str(answer),
                        "Включение-исключение: 53−16+2=39; 31−9+1=23.", oracle=dict(counts=counts)))
    for first, second, index, answer in [(2, 3, 4, 46), (1, 4, 5, 128)]:
        out.append(Case("math", "recurrence",
                        f"Let a(0)={first}, a(1)={second}, and a(n)=2*a(n-1)+a(n-2) for n>=2. "
                        f"Find a({index}).", str(answer),
                        "Ручные ряды: 2,3,8,19,46 и 1,4,9,22,53,128.",
                        oracle=dict(first=first, second=second, index=index)))
    out += [Case("math", "missing_initial", "a(n)=2*a(n-1)+a(n-2), a(0)=2. Find a(4).", None,
                 "a(1) не задано: значения 0 и 1 дают разные a(4)."),
            Case("math", "inconsistent_remainders", "Find an integer x with x mod 4=1 and x mod 2=0.", None,
                 "Первое условие требует нечётности, второе — чётности.")]
    return out
