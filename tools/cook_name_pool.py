"""Cook the checked-in name pool. Build-time only; Faker is not a runtime dependency."""
import re
import textwrap
import warnings

warnings.filterwarnings("ignore")
from faker import Faker
from unidecode import unidecode

LOCALES = {
    "bengali":"bn_BD","czech":"cs_CZ","german":"de_DE","greek":"el_GR","english":"en_GB",
    "spanish":"es_MX","finnish":"fi_FI","french":"fr_FR","hindi":"hi_IN","hungarian":"hu_HU",
    "indonesian":"id_ID","italian":"it_IT","japanese":"ja_JP","korean":"ko_KR","nepali":"ne_NP",
    "dutch":"nl_NL","norwegian":"no_NO","polish":"pl_PL","portuguese":"pt_BR","romanian":"ro_RO",
    "russian":"ru_RU","swedish":"sv_SE","swahili":"sw","turkish":"tr_TR","ukrainian":"uk_UA",
    "chinese":"zh_CN",
}
# Abjads and Tamil: machine transliteration yields consonant clusters or 27-char forms,
# so these are conventional romanisations, curated by hand.
CURATED = {
 "arabic": (["mahmood","farida","yusuf","layla","tariq","amina","hassan","zaynab","khalid",
   "nadia","omar","rania","samir","hala","idris","salma","jamila","bilal","noor","kareem"],
  ["mansour","haddad","nasser","khoury","aziz","farouk","rahman","saleh","bakri","darwish",
   "jaber","qasim","sabbagh","zahra","hakim","murad","sultan","othman"]),
 "persian": (["reza","shirin","kaveh","roya","darius","parisa","farhad","mitra","arash",
   "nasrin","babak","yasmin","soraya","cyrus","laleh","payam"],
  ["hosseini","tehrani","shirazi","nourani","esfahani","kermani","farahani","sadeghi",
   "moradi","rostami","ansari","zand","navid","parvin"]),
 "hebrew": (["yitzhak","tamar","eitan","noa","avram","shoshana","gideon","yael","boaz",
   "michal","asher","dalia","ezra","naomi","reuven","talia"],
  ["bendavid","shapiro","katz","levin","mizrahi","abadi","rosen","peretz","adler","barlev",
   "gutman","harari","segal","weiss"]),
 "tamil": (["arun","kavya","murugan","priya","senthil","meena","ravi","lakshmi","karthik",
   "divya","vijay","anitha","suresh","kamala","bala","nithya"],
  ["subramanian","raman","iyer","pillai","nadar","chelvam","rajan","kannan","murthy",
   "selvam","natarajan","venkatesan"]),
}
VALID = re.compile(r"^[a-z]{3,14}$")   # single token only — a name is given_family

def clean(raw):
    return re.sub(r"[^a-z]", "", unidecode(raw).lower())

def legible(w):
    return bool(VALID.match(w)) and sum(c in "aeiou" for c in w)/len(w) >= 0.30

pool = {}
for tradition, loc in LOCALES.items():
    f = Faker(loc); f.seed_instance(20260724)
    given, family = set(), set()
    for _ in range(4000):
        for src, bucket in ((f.first_name(), given), (f.last_name(), family)):
            w = clean(src)
            if legible(w):
                bucket.add(w)
        if len(given) >= 20 and len(family) >= 20:
            break
    if len(given) >= 10 and len(family) >= 10:
        pool[tradition] = (sorted(given)[:20], sorted(family)[:20])
    else:
        print(f"  ! dropped {tradition} (given={len(given)} family={len(family)})")
pool.update({k: (sorted(v[0]), sorted(v[1])) for k, v in CURATED.items()})

lines = []
for t in sorted(pool):
    g, fam = pool[t]
    lines.append(f'    "{t}": (')
    for label, words in (("given", g), ("family", fam)):
        body = ", ".join(f'"{w}"' for w in words)
        wrapped = textwrap.fill(body, 84, initial_indent=" "*8, subsequent_indent=" "*12)
        lines.append(f"        (  # {label}\n{wrapped}\n        ),")
    lines.append("    ),")
open("/private/tmp/claude-501/-Volumes-Home-work-agent-mail/a8159256-43d0-4059-9873-6d0874ccd82e/scratchpad/pool_body.txt","w").write("\n".join(lines))
tot_g = sum(len(g) for g,_ in pool.values()); tot_f = sum(len(f) for _,f in pool.values())
print(f"traditions={len(pool)}  given={tot_g}  family={tot_f}  combinations={tot_g*tot_f:,}")
