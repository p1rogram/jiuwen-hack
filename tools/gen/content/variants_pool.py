"""Контент набора вариантов (новые формулировки и виды вопросов тех же 4 категорий). Только данные.

math: `text` — шаблон условия, `params` — диапазоны [lo, hi] или список значений, `where` — условие корректности,
`answer` — выражение Python от параметров (эталон считает генератор). data/search: формулировки вопросов по видам;
сам ответ считает `tools/gen/variants.py` по сгенерированным файлам. coding — `variants_coding.py`.
"""

TAILS = [
    " Work only inside the current directory. When you are done, write ONLY the final answer to the file "
    "answer.txt in the current directory (for example with bash: echo 42 > answer.txt).",
    " Save only the answer, with nothing else, to answer.txt in the current directory.",
    " Write just the result into a file named answer.txt in the working directory.",
]
CODE_TAIL = (" Work only inside the current directory. When you are done, write ONLY the word DONE to the file "
             "answer.txt in the current directory (for example with bash: echo 42 > answer.txt).")

MATH = [
    {"text": "A library had {a} books. On Monday it lent out one quarter of them. On Tuesday {b} of the lent books "
             "were returned. How many books are in the library now?",
     "params": {"a": [200, 900], "b": [5, 50]}, "where": "a % 4 == 0 and b <= a // 4", "answer": "a - a // 4 + b"},
    {"text": "Tom saves {a} dollars every week for {w} weeks, then spends {p}% of his savings on a bike. "
             "How many dollars does he have left?",
     "params": {"a": [10, 40], "w": [4, 20], "p": [10, 20, 25, 40, 50]}, "where": "a * w * p % 100 == 0",
     "answer": "a * w - a * w * p // 100"},
    {"text": "A train leaves the station with {a} passengers. At the first stop {b} people get off and {c} get on. "
             "At the second stop one third of the passengers get off. How many passengers remain on the train?",
     "params": {"a": [120, 400], "b": [10, 60], "c": [5, 40]}, "where": "(a - b + c) % 3 == 0",
     "answer": "(a - b + c) - (a - b + c) // 3"},
    {"text": "A depot holds {a} crates. Every day for {d} days it dispatches {b} crates. After that it gets {k} "
             "shipments with {c} crates each. What is the final number of crates? Use python3 for the arithmetic "
             "and save just the number in answer.txt.",
     "params": {"a": [500, 2000], "d": [3, 9], "b": [20, 90], "k": [2, 5], "c": [15, 80]}, "where": "a > b * d",
     "answer": "a - b * d + k * c", "no_tail": True},
    {"text": "A factory produces {r} widgets per hour and runs {h} hours a day. How many widgets does it produce "
             "in {d} days?",
     "params": {"r": [40, 300], "h": [6, 16], "d": [2, 12]}, "answer": "r * h * d"},
    {"text": "A water tank holds {a} liters. It leaks {b} liters per hour for {h} hours, and then a truck adds "
             "{c} liters. How many liters are in the tank now?",
     "params": {"a": [300, 2000], "b": [5, 40], "h": [3, 12], "c": [50, 400]}, "where": "a > b * h",
     "answer": "a - b * h + c"},
    {"text": "Maria is {a} years old. Her brother is {b} years younger than her. What will the sum of their ages "
             "be in {c} years?",
     "params": {"a": [12, 60], "b": [2, 10], "c": [3, 15]}, "where": "a > b + 1", "answer": "a + (a - b) + 2 * c"},
    {"text": "Pens cost {p} cents each. Alice buys {n} pens and pays with a {d}-dollar bill. How much change does "
             "she get, in cents?",
     "params": {"p": [35, 180], "n": [2, 9], "d": [5, 10, 20]}, "where": "d * 100 > p * n",
     "answer": "d * 100 - p * n"},
    {"text": "A student scored {a}, {b}, {c} and {d} on four tests. What score does the student need on the fifth "
             "test to have an average of exactly {m} over all five tests?",
     "params": {"a": [55, 98], "b": [55, 98], "c": [55, 98], "d": [55, 98], "m": [70, 90]},
     "where": "0 <= 5 * m - (a + b + c + d) <= 100", "answer": "5 * m - (a + b + c + d)"},
    {"text": "Each cake needs {g} grams of flour. A baker has {k} kilograms of flour. How many whole cakes can "
             "she bake?",
     "params": {"g": [180, 450], "k": [3, 25]}, "where": "k * 1000 % g != 0", "answer": "k * 1000 // g"},
    {"text": "A town has {n} residents. Its population grows by {p}% every year. How many residents will it have "
             "after 2 years? Round down to a whole number after each year.",
     "params": {"n": [1200, 40000], "p": [2, 9]}, "answer": "(lambda y: y + y * p // 100)(n + n * p // 100)"},
    {"text": "A cyclist rides at {s} km/h for {h} hours, takes a break, and then rides at {t} km/h for {k} hours. "
             "How many kilometers does she ride in total?",
     "params": {"s": [12, 30], "h": [1, 5], "t": [10, 28], "k": [1, 4]}, "answer": "s * h + t * k"},
    {"text": "A parking garage has {f} floors with {s} spaces on each floor. {c} cars are parked there. How many "
             "spaces are free?",
     "params": {"f": [2, 8], "s": [20, 90], "c": [10, 500]}, "where": "c < f * s", "answer": "f * s - c"},
    {"text": "{x} dollars are shared between Ann and Ben in the ratio {a}:{b}. How many dollars does Ben receive?",
     "params": {"x": [100, 2000], "a": [1, 7], "b": [1, 7]}, "where": "x % (a + b) == 0 and a != b",
     "answer": "x // (a + b) * b"},
    {"text": "After spending {p}% of his money, Leo has {r} dollars left. How many dollars did he have at first?",
     "params": {"p": [10, 20, 25, 40, 50, 60, 75], "r": [30, 900]}, "where": "r * 100 % (100 - p) == 0",
     "answer": "r * 100 // (100 - p)"},
    {"text": "A printer prints {a} pages per minute. How many minutes does it need to print {n} copies of a "
             "{k}-page report? Round up to a whole minute.",
     "params": {"a": [8, 40], "n": [5, 60], "k": [3, 30]}, "where": "n * k % a != 0", "answer": "-(-n * k // a)"},
]
MATH_INFEASIBLE = [
    {"text": "A shop has a crate of apples. It sells {a} apples in the morning and {b} in the evening. How many "
             "apples are left in the crate?", "params": {"a": [5, 30], "b": [5, 30]}},
    {"text": "A water tank starts with {a} liters. It leaks at a steady rate for {h} hours. How many liters are "
             "left in the tank?", "params": {"a": [200, 900], "h": [2, 9]}},
]

DATA_INTROS = {
    "employees": ["The file employees.csv lists staff with the columns {cols}.",
                  "employees.csv has one row per employee ({cols}).",
                  "Using employees.csv (columns {cols}),"],
    "sales": ["The file sales.csv has columns {cols} ({n} rows).",
              "sales.csv records orders with the columns {cols}.",
              "In sales.csv ({cols}),"],
    "inventory": ["The file inventory.csv has columns {cols}.",
                  "inventory.csv describes the stock of a store ({cols})."],
    "grades": ["The file grades.csv has columns {cols}; each row is one exam result.",
               "grades.csv stores exam results ({cols})."],
}
DATA_Q = {
    "avg_salary_dept": "What is the average salary in the '{dept}' department, rounded to the nearest integer?",
    "count_years_over": "How many employees have more than {k} years of experience?",
    "top_dept_total": "Which department has the highest total salary? Answer with the department name.",
    "top_earner_dept": "Who is the best-paid employee in the '{dept}' department? Answer with the name.",
    "median_salary": "What is the median salary over all employees?",
    "salary_range_dept": "What is the difference between the highest and the lowest salary in the '{dept}' "
                         "department?",
    "max_units_product": "What was the largest number of units sold in a single order of product '{product}'?",
    "revenue_region": "Compute the total revenue (units multiplied by price) of all orders from region '{region}', "
                      "over all months.",
    "distinct_products_region": "How many different products were ordered in region '{region}'?",
    "share_region": "What percentage of all orders came from region '{region}'? Round to the nearest integer and "
                    "write the number without the % sign.",
    "top_month_units": "In which month (give the month number) was the total number of units sold the largest?",
    "units_months_product": "Between month {m1} and month {m2} (both included), how many units of '{product}' were "
                            "sold in total?",
    "below_reorder": "How many items have a stock lower than their reorder_level?",
    "category_value": "What is the total value (stock multiplied by unit_cost) of all items in the '{category}' "
                      "category?",
    "avg_score_student": "What is {student}'s average score over all subjects, rounded to the nearest integer?",
    "top_student_subject": "Which student got the highest score in {subject}? Answer with the student's name.",
}
DATA_INFEASIBLE = [
    {"table": "employees", "declared": "bonus",
     "question": "What is the total bonus paid to the '{dept}' department?"},
    {"table": "sales", "declared": "discount",
     "question": "What is the total discount given on orders from region '{region}'?"},
]
DEPARTMENTS = ["engineering", "sales", "support", "marketing", "finance"]
SUBJECTS = ["math", "physics", "history", "biology", "art"]
CATEGORIES = ["tools", "garden", "kitchen", "toys"]
ITEMS = ["hammer", "rake", "kettle", "puzzle", "drill", "hose", "pan", "kite", "saw", "shovel", "mug", "ball",
         "wrench", "trowel", "whisk", "yo-yo", "pliers", "seeds", "plate", "robot"]

# поля карточек kb: несколько словарей названий, чтобы не учить одну форму
KB_FIELDS = [
    {"born": "Date of birth", "employer": "Works at", "mentor": "Advisor",
     "city": "Location", "founded": "Established", "head": "CEO"},
    {"born": "Born", "employer": "Employer", "mentor": "Mentor",
     "city": "Headquarters", "founded": "Founded", "head": "Chief executive"},
    {"born": "Year of birth", "employer": "Company", "mentor": "Supervisor",
     "city": "City", "founded": "Founded in", "head": "Director"},
]
KB_INTROS = [
    "The directory kb/ contains a small knowledge base of markdown files. ",
    "Notes about people and companies are stored as markdown files in kb/. ",
    "Use the knowledge base in the kb/ folder (one markdown file per person or company). ",
]
KB_TOOL_HINT = " Use the file tools or bash (ls, grep, cat) to find it."
SEARCH_Q = {
    "head_of_employer": "Who is the {head} of the company where {p} works?",
    "mentor_born": "What year was the {mentor} of {p} born?",
    "employer_city": "Which city is home to the company that employs {p}?",
    "count_employees": "How many people in the knowledge base work at {c}?",
    "born_first": "Who was born first, {p} or {q}?",
    "oldest_company": "Which company in the knowledge base was established earliest?",
    "mentor_employer_city": "In which city is the company of {p}'s {mentor} located?",
    "mentor_of_head": "Who is the {mentor} of the {head} of {c}?",
    "age_at_founding": "How old was {p} in the year {c} was established (difference of the years)?",
    "youngest_person": "Which person in the knowledge base was born most recently?",
    "founding_gap": "How many years after {c} was {d} established?",
}
SEARCH_INFEASIBLE = ["In which year was {phantom} born?", "In which year was Alexander Pushkin born?"]
