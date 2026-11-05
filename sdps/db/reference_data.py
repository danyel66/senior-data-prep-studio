"""Static reference data (small dimension tables and name pools) for the AdventureWorks seed."""
from __future__ import annotations

COUNTRIES = [("US", "United States"), ("CA", "Canada"), ("FR", "France"), ("DE", "Germany"),
             ("AU", "Australia"), ("GB", "United Kingdom")]

# territory_id, name, country, group, share of customers (deliberately skewed, sums to 1.0)
TERRITORIES = [
    (1, "Northwest", "US", "North America", 0.14),
    (2, "Northeast", "US", "North America", 0.02),
    (3, "Central", "US", "North America", 0.04),
    (4, "Southwest", "US", "North America", 0.28),
    (5, "Southeast", "US", "North America", 0.03),
    (6, "Canada", "CA", "North America", 0.18),
    (7, "France", "FR", "Europe", 0.07),
    (8, "Germany", "DE", "Europe", 0.04),
    (9, "Australia", "AU", "Pacific", 0.12),
    (10, "United Kingdom", "GB", "Europe", 0.08),
]
# territory -> currency index used by sales.currency_rate (None = USD, no conversion)
TERRITORY_CURRENCY = {6: 0, 7: 1, 8: 1, 9: 2, 10: 3}
RATE_CURRENCIES = [("CAD", 1.30), ("EUR", 0.90), ("AUD", 1.45), ("GBP", 0.78)]   # index order matters
CURRENCIES = [("USD", "US Dollar"), ("CAD", "Canadian Dollar"), ("EUR", "Euro"),
              ("AUD", "Australian Dollar"), ("GBP", "United Kingdom Pound")]

# code, country, name, territory, cities
STATES = [
    ("WA", "US", "Washington", 1, "Seattle|Bellevue|Bothell|Redmond|Spokane|Tacoma"),
    ("OR", "US", "Oregon", 1, "Portland|Salem|Eugene|Beaverton"),
    ("ID", "US", "Idaho", 1, "Boise|Nampa"),
    ("MT", "US", "Montana", 1, "Billings|Missoula"),
    ("NY", "US", "New York", 2, "New York|Buffalo|Albany|Rochester"),
    ("MA", "US", "Massachusetts", 2, "Boston|Cambridge|Worcester"),
    ("PA", "US", "Pennsylvania", 2, "Philadelphia|Pittsburgh"),
    ("NJ", "US", "New Jersey", 2, "Newark|Trenton"),
    ("IL", "US", "Illinois", 3, "Chicago|Springfield|Naperville"),
    ("OH", "US", "Ohio", 3, "Columbus|Cleveland|Cincinnati"),
    ("MI", "US", "Michigan", 3, "Detroit|Ann Arbor|Lansing"),
    ("MN", "US", "Minnesota", 3, "Minneapolis|Saint Paul"),
    ("CA", "US", "California", 4, "Los Angeles|San Diego|San Francisco|San Jose|Sacramento|Irvine|Berkeley"),
    ("AZ", "US", "Arizona", 4, "Phoenix|Tucson|Mesa"),
    ("NV", "US", "Nevada", 4, "Las Vegas|Reno"),
    ("UT", "US", "Utah", 4, "Salt Lake City|Provo"),
    ("TX", "US", "Texas", 4, "Austin|Dallas|Houston|San Antonio"),
    ("FL", "US", "Florida", 5, "Miami|Orlando|Tampa|Jacksonville"),
    ("GA", "US", "Georgia", 5, "Atlanta|Savannah"),
    ("NC", "US", "North Carolina", 5, "Charlotte|Raleigh"),
    ("TN", "US", "Tennessee", 5, "Nashville|Memphis"),
    ("BC", "CA", "British Columbia", 6, "Vancouver|Victoria|Burnaby|Surrey"),
    ("ON", "CA", "Ontario", 6, "Toronto|Ottawa|Mississauga"),
    ("QC", "CA", "Quebec", 6, "Montreal|Quebec City"),
    ("AB", "CA", "Alberta", 6, "Calgary|Edmonton"),
    ("75", "FR", "Seine (Paris)", 7, "Paris"),
    ("59", "FR", "Nord", 7, "Lille|Roubaix"),
    ("92", "FR", "Hauts de Seine", 7, "Boulogne|Colombes|Nanterre"),
    ("69", "FR", "Rhone", 7, "Lyon|Villeurbanne"),
    ("HE", "DE", "Hessen", 8, "Frankfurt|Wiesbaden"),
    ("NW", "DE", "Nordrhein-Westfalen", 8, "Cologne|Dusseldorf|Essen"),
    ("BY", "DE", "Bayern", 8, "Munich|Nuremberg"),
    ("HH", "DE", "Hamburg", 8, "Hamburg"),
    ("NSW", "AU", "New South Wales", 9, "Sydney|Newcastle|Wollongong"),
    ("VIC", "AU", "Victoria", 9, "Melbourne|Geelong"),
    ("QLD", "AU", "Queensland", 9, "Brisbane|Gold Coast"),
    ("SA", "AU", "South Australia", 9, "Adelaide"),
    ("ENG", "GB", "England", 10, "London|Manchester|Birmingham|Bristol|Leeds"),
    ("SCT", "GB", "Scotland", 10, "Edinburgh|Glasgow"),
]

DEPARTMENTS = [
    (1, "Engineering", "Research and Development"),
    (2, "Tool Design", "Research and Development"),
    (3, "Sales", "Sales and Marketing"),
    (4, "Marketing", "Sales and Marketing"),
    (5, "Purchasing", "Inventory Management"),
    (6, "Research and Development", "Research and Development"),
    (7, "Production", "Manufacturing"),
    (8, "Production Control", "Manufacturing"),
    (9, "Human Resources", "Executive General and Administration"),
    (10, "Finance", "Executive General and Administration"),
    (11, "Information Services", "Executive General and Administration"),
    (12, "Document Control", "Quality Assurance"),
    (13, "Quality Assurance", "Quality Assurance"),
    (14, "Facilities and Maintenance", "Executive General and Administration"),
    (15, "Shipping and Receiving", "Inventory Management"),
    (16, "Executive", "Executive General and Administration"),
]
CEO_DEPARTMENT, SALES_DEPARTMENT, PURCHASING_DEPARTMENT, PRODUCTION_DEPARTMENT = 16, 3, 5, 7

CATEGORIES = [(1, "Bikes"), (2, "Components"), (3, "Clothing"), (4, "Accessories")]
# category -> (min list price, max list price, manufactured in-house?)
CATEGORY_ECONOMICS = {1: (540.0, 3580.0, True), 2: (20.0, 1430.0, True),
                      3: (8.0, 90.0, False), 4: (2.3, 160.0, False)}
# subcategory_id, category_id, name, singular model name, weight in assortment
SUBCATEGORIES = [
    (1, 1, "Mountain Bikes", "Mountain", 4), (2, 1, "Road Bikes", "Road", 5),
    (3, 1, "Touring Bikes", "Touring", 3),
    (4, 2, "Handlebars", "Handlebars", 1), (5, 2, "Bottom Brackets", "Bottom Bracket", 1),
    (6, 2, "Brakes", "Brakes", 1), (7, 2, "Chains", "Chain", 1), (8, 2, "Cranksets", "Crankset", 1),
    (9, 2, "Derailleurs", "Derailleur", 1), (10, 2, "Forks", "Fork", 1), (11, 2, "Headsets", "Headset", 1),
    (12, 2, "Mountain Frames", "Mountain Frame", 3), (13, 2, "Pedals", "Pedal", 1),
    (14, 2, "Road Frames", "Road Frame", 3), (15, 2, "Saddles", "Seat", 1),
    (16, 2, "Touring Frames", "Touring Frame", 2), (17, 2, "Wheels", "Wheel", 2),
    (18, 3, "Bib-Shorts", "Bib-Shorts", 1), (19, 3, "Caps", "Cycling Cap", 1), (20, 3, "Gloves", "Gloves", 1),
    (21, 3, "Jerseys", "Jersey", 2), (22, 3, "Shorts", "Shorts", 1), (23, 3, "Socks", "Socks", 1),
    (24, 3, "Tights", "Tights", 1), (25, 3, "Vests", "Vest", 1),
    (26, 4, "Bike Racks", "Hitch Rack", 1), (27, 4, "Bike Stands", "Bike Stand", 1),
    (28, 4, "Bottles and Cages", "Bottle Cage", 1), (29, 4, "Cleaners", "Bike Wash", 1),
    (30, 4, "Fenders", "Fender Set", 1), (31, 4, "Helmets", "Sport Helmet", 1),
    (32, 4, "Hydration Packs", "Hydration Pack", 1), (33, 4, "Lights", "Headlights", 1),
    (34, 4, "Locks", "Cable Lock", 1), (35, 4, "Panniers", "Touring Pannier", 1),
    (36, 4, "Pumps", "Minipump", 1), (37, 4, "Tires and Tubes", "Tire", 2),
]
RAW_PART_NAMES = [
    "Adjustable Race", "Bearing Ball", "BB Ball Bearing", "Headset Ball Bearings", "Blade", "LL Crankarm",
    "ML Crankarm", "HL Crankarm", "Chainring Bolts", "Chainring Nut", "Chainring", "Crown Race",
    "Chain Stays", "Decal", "Down Tube", "Mountain End Caps", "Road End Caps", "Touring End Caps",
    "Fork End", "Freewheel", "Flat Washer", "Lock Washer", "Hex Nut", "Lock Nut", "Thin-Jam Hex Nut",
    "Cup-Shaped Race", "Cone-Shaped Race", "Front Derailleur Cage", "Front Derailleur Linkage",
    "Guide Pulley", "LL Grip Tape", "ML Grip Tape", "HL Grip Tape", "Head Tube", "Keyed Washer",
    "External Lock Washer", "Internal Lock Washer", "Lower Head Race", "Metal Angle", "Metal Bar",
    "Metal Plate", "Metal Sheet", "Rear Derailleur Cage", "Reflector", "Seat Lug", "Seat Stays",
    "Seat Tube", "Spokes", "Stem", "Top Tube", "Tension Pulley", "Rim", "Hub", "Pinch Bolt",
    "Paint - Black", "Paint - Red", "Paint - Silver", "Paint - Blue", "Paint - Yellow", "Steerer",
]
N_RAW_PARTS = 200
N_FINISHED_PRODUCTS = 304
FINISHED_PRODUCT_ID_START = 680       # mirrors AdventureWorks numbering
COLORS = ["Black", "Red", "Silver", "Blue", "Yellow", "White", "Multi", "Grey"]
BIKE_SIZES = ["38", "40", "42", "44", "46", "48", "50", "52", "54", "56", "58", "60", "62"]
APPAREL_SIZES = ["S", "M", "L", "XL"]

LOCATIONS = [
    (1, "Tool Crib", 0.0, 0.0), (2, "Sheet Metal Racks", 0.0, 0.0), (3, "Paint Shop", 0.0, 0.0),
    (4, "Paint Storage", 0.0, 0.0), (5, "Metal Storage", 0.0, 0.0), (6, "Miscellaneous Storage", 0.0, 0.0),
    (7, "Finished Goods Storage", 0.0, 0.0), (10, "Frame Forming", 22.5, 96.0),
    (20, "Frame Welding", 25.0, 108.0), (30, "Debur and Polish", 14.5, 120.0), (40, "Paint", 15.75, 120.0),
    (45, "Specialized Paint", 18.0, 80.0), (50, "Subassembly", 12.25, 120.0), (60, "Final Assembly", 12.25, 120.0),
]

SHIP_METHODS = [(1, "XRQ - TRUCK GROUND", 3.95, 0.99), (2, "ZY - EXPRESS", 9.95, 1.99),
                (3, "OVERSEAS - DELUXE", 29.95, 2.99), (4, "OVERNIGHT J-FAST", 21.95, 1.29),
                (5, "CARGO TRANSPORT 5", 8.99, 1.49)]
SHIP_METHOD_ONLINE, SHIP_METHOD_RESELLER = 1, 5

# id, description, discount, type, category, min_qty, max_qty
SPECIAL_OFFERS = [
    (1, "No Discount", 0.00, "No Discount", "No Discount", 0, None),
    (2, "Volume Discount 11 to 14", 0.02, "Volume Discount", "Reseller", 11, 14),
    (3, "Volume Discount 15 to 24", 0.05, "Volume Discount", "Reseller", 15, 24),
    (4, "Volume Discount 25 to 40", 0.10, "Volume Discount", "Reseller", 25, 40),
    (5, "Volume Discount 41 to 60", 0.15, "Volume Discount", "Reseller", 41, 60),
    (6, "Volume Discount over 60", 0.20, "Volume Discount", "Reseller", 61, None),
    (7, "Mountain-100 Clearance Sale", 0.35, "Discontinued Product", "Reseller", 0, None),
    (8, "Sport Helmet Discount", 0.10, "Seasonal Discount", "Reseller", 0, None),
    (9, "Road-650 Overstock", 0.30, "Excess Inventory", "Reseller", 0, None),
    (10, "Mountain Tire Sale", 0.50, "Excess Inventory", "Customer", 0, None),
    (11, "Sport Helmet Discount Spring", 0.15, "Seasonal Discount", "Reseller", 0, None),
    (12, "LL Road Frame Sale", 0.35, "Excess Inventory", "Reseller", 0, None),
    (13, "Touring-3000 Promotion", 0.15, "New Product", "Customer", 0, None),
    (14, "Touring-1000 Promotion", 0.20, "New Product", "Customer", 0, None),
    (15, "Half-Price Pedal Sale", 0.50, "Seasonal Discount", "Customer", 0, None),
    (16, "Mountain-500 Silver Clearance Sale", 0.40, "Discontinued Product", "Reseller", 0, None),
]

FIRST_NAMES = [
    "James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda", "David", "Elizabeth",
    "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Carlos", "Karen",
    "Wei", "Mei", "Hiroshi", "Yuki", "Arjun", "Priya", "Mohammed", "Fatima", "Olu", "Ngozi", "Chidi", "Amara",
    "Lucas", "Emma", "Mateo", "Sofia", "Liam", "Olivia", "Noah", "Ava", "Pierre", "Camille", "Hans", "Greta",
    "Diego", "Valentina", "Ivan", "Anya", "Kwame", "Abena", "Sean", "Aoife", "Raj", "Ananya", "Tom", "Chloe",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
    "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Chen", "Wang", "Kim", "Nguyen", "Patel", "Singh", "Kumar", "Okafor", "Adeyemi", "Nwosu", "Mensah",
    "Muller", "Schmidt", "Dubois", "Moreau", "Rossi", "Bianchi", "Silva", "Santos", "Tanaka", "Suzuki",
    "Ivanov", "Kowalski", "O'Brien", "Murphy", "Campbell", "Stewart", "Clarke", "Walker", "Young", "King",
]
STREETS = ["Main", "Oak", "Pine", "Maple", "Cedar", "Elm", "Lake", "Hill", "Park", "Washington", "River",
           "Sunset", "Highland", "Valley", "Mill", "Church", "Spring", "Forest", "Meadow", "Bay"]
STREET_SUFFIXES = ["St.", "Ave.", "Blvd.", "Rd.", "Dr.", "Ln.", "Way", "Ct."]
STORE_ADJECTIVES = ["Pro", "Metro", "Valley", "Summit", "Urban", "Coastal", "Alpine", "Rapid", "Classic", "Premier",
                    "Mountain", "Trail", "City", "Speedy", "Elite", "Family", "Local", "Global", "Victory", "Golden"]
STORE_NOUNS = ["Bike Shop", "Cycles", "Sporting Goods", "Bike Works", "Outfitters", "Wheels", "Pedal Co.",
               "Cycle Center", "Bicycle Supply", "Sports", "Riders", "Gear Exchange", "Bike Depot",
               "Cyclery", "Bikes and Boards", "Spokes", "Velo", "Bike Gallery", "Fitness", "Trading Co."]
VENDOR_PREFIXES = ["Advanced", "Allied", "American", "Australian", "Bicycle", "Capital", "Compete", "Continental",
                   "Crowley", "Electronic", "First", "Fitness", "Global", "Greenwood", "Hill", "Integrated",
                   "International", "Jackson", "Lakewood", "Midwest", "National", "Northern", "Pacific",
                   "Premier", "Professional", "Signature", "Superior", "Team", "Trey", "Wide World"]
VENDOR_SUFFIXES = ["Bike Components", "Cycles", "Sport Supply", "Manufacturing", "Distributors", "Parts",
                   "Industries", "Metal Works", "Specialties", "Supply Co.", "Imports", "Hardware"]
