-- AdventureWorks (adapted) · portable DDL for DuckDB and PostgreSQL
-- Identifiers are snake_case. Hierarchies use manager_id instead of SQL Server hierarchyid.
-- Constraints live in constraints.sql and are applied after bulk load.
-- Statements are separated by a semicolon at end of line, and no comments contain semicolons.

CREATE SCHEMA IF NOT EXISTS person;
CREATE SCHEMA IF NOT EXISTS humanresources;
CREATE SCHEMA IF NOT EXISTS production;
CREATE SCHEMA IF NOT EXISTS purchasing;
CREATE SCHEMA IF NOT EXISTS sales;

-- ─────────────────────────────── person ───────────────────────────────
CREATE TABLE person.country_region (
    country_region_code VARCHAR(3)   NOT NULL,
    name                VARCHAR(50)  NOT NULL
);
COMMENT ON TABLE person.country_region IS 'ISO country codes.';

CREATE TABLE person.state_province (
    state_province_id   INTEGER      NOT NULL,
    state_province_code VARCHAR(3)   NOT NULL,
    country_region_code VARCHAR(3)   NOT NULL,
    name                VARCHAR(50)  NOT NULL,
    territory_id        INTEGER      NOT NULL
);
COMMENT ON TABLE person.state_province IS 'States and provinces, each mapped to a sales territory.';

CREATE TABLE person.address (
    address_id          INTEGER      NOT NULL,
    address_line1       VARCHAR(60)  NOT NULL,
    address_line2       VARCHAR(60),
    city                VARCHAR(30)  NOT NULL,
    state_province_id   INTEGER      NOT NULL,
    postal_code         VARCHAR(15)  NOT NULL,
    modified_date       TIMESTAMP    NOT NULL
);
COMMENT ON TABLE person.address IS 'Street addresses for people and stores. address_line2 is mostly NULL.';

CREATE TABLE person.person (
    business_entity_id  INTEGER      NOT NULL,
    person_type         VARCHAR(2)   NOT NULL,
    title               VARCHAR(8),
    first_name          VARCHAR(50)  NOT NULL,
    middle_name         VARCHAR(50),
    last_name           VARCHAR(50)  NOT NULL,
    email_promotion     SMALLINT     NOT NULL,
    address_id          INTEGER      NOT NULL,
    modified_date       TIMESTAMP    NOT NULL
);
COMMENT ON TABLE person.person IS 'People. person_type: EM employee, SP sales person, IN individual customer.';

CREATE TABLE person.email_address (
    business_entity_id  INTEGER      NOT NULL,
    email_address_id    INTEGER      NOT NULL,
    email_address       VARCHAR(80)  NOT NULL,
    modified_date       TIMESTAMP    NOT NULL
);
COMMENT ON TABLE person.email_address IS 'Emails. About 3 percent of people have none and about 2 percent have two.';

-- ─────────────────────────── humanresources ───────────────────────────
CREATE TABLE humanresources.department (
    department_id       SMALLINT     NOT NULL,
    name                VARCHAR(50)  NOT NULL,
    group_name          VARCHAR(50)  NOT NULL
);
COMMENT ON TABLE humanresources.department IS 'Company departments.';

CREATE TABLE humanresources.employee (
    business_entity_id  INTEGER      NOT NULL,
    national_id_number  VARCHAR(15)  NOT NULL,
    login_id            VARCHAR(256) NOT NULL,
    manager_id          INTEGER,
    organization_level  SMALLINT     NOT NULL,
    job_title           VARCHAR(50)  NOT NULL,
    birth_date          DATE         NOT NULL,
    marital_status      VARCHAR(1)   NOT NULL,
    gender              VARCHAR(1)   NOT NULL,
    hire_date           DATE         NOT NULL,
    salaried_flag       BOOLEAN      NOT NULL,
    vacation_hours      SMALLINT     NOT NULL,
    sick_leave_hours    SMALLINT     NOT NULL,
    current_flag        BOOLEAN      NOT NULL
);
COMMENT ON TABLE humanresources.employee IS 'Employees. manager_id forms an org tree rooted at the CEO (use a recursive CTE).';

CREATE TABLE humanresources.employee_department_history (
    business_entity_id  INTEGER      NOT NULL,
    department_id       SMALLINT     NOT NULL,
    start_date          DATE         NOT NULL,
    end_date            DATE
);
COMMENT ON TABLE humanresources.employee_department_history IS 'Type-2 history of department assignments. end_date NULL means current.';

CREATE TABLE humanresources.employee_pay_history (
    business_entity_id  INTEGER       NOT NULL,
    rate_change_date    DATE          NOT NULL,
    rate                DECIMAL(19,4) NOT NULL,
    pay_frequency       SMALLINT      NOT NULL
);
COMMENT ON TABLE humanresources.employee_pay_history IS 'Hourly pay rate changes. pay_frequency 1 monthly, 2 biweekly.';

-- ───────────────────────────── production ─────────────────────────────
CREATE TABLE production.product_category (
    product_category_id INTEGER      NOT NULL,
    name                VARCHAR(50)  NOT NULL
);
COMMENT ON TABLE production.product_category IS 'Top-level categories: Bikes, Components, Clothing, Accessories.';

CREATE TABLE production.product_subcategory (
    product_subcategory_id INTEGER   NOT NULL,
    product_category_id    INTEGER   NOT NULL,
    name                   VARCHAR(50) NOT NULL
);
COMMENT ON TABLE production.product_subcategory IS 'Subcategories within a category.';

CREATE TABLE production.product (
    product_id             INTEGER       NOT NULL,
    name                   VARCHAR(50)   NOT NULL,
    product_number         VARCHAR(25)   NOT NULL,
    make_flag              BOOLEAN       NOT NULL,
    finished_goods_flag    BOOLEAN       NOT NULL,
    color                  VARCHAR(15),
    safety_stock_level     SMALLINT      NOT NULL,
    reorder_point          SMALLINT      NOT NULL,
    standard_cost          DECIMAL(19,4) NOT NULL,
    list_price             DECIMAL(19,4) NOT NULL,
    size                   VARCHAR(5),
    weight                 DECIMAL(8,2),
    product_subcategory_id INTEGER,
    sell_start_date        DATE          NOT NULL,
    sell_end_date          DATE,
    discontinued_date      DATE
);
COMMENT ON TABLE production.product IS 'Products. Raw parts have finished_goods_flag false and no subcategory. list_price is the current price.';

CREATE TABLE production.product_list_price_history (
    product_id          INTEGER       NOT NULL,
    start_date          DATE          NOT NULL,
    end_date            DATE,
    list_price          DECIMAL(19,4) NOT NULL
);
COMMENT ON TABLE production.product_list_price_history IS 'Type-2 list price history, one row per product per year. end_date NULL means current.';

CREATE TABLE production.location (
    location_id         SMALLINT      NOT NULL,
    name                VARCHAR(50)   NOT NULL,
    cost_rate           DECIMAL(10,4) NOT NULL,
    availability        DECIMAL(8,2)  NOT NULL
);
COMMENT ON TABLE production.location IS 'Warehouse and manufacturing locations.';

CREATE TABLE production.product_inventory (
    product_id          INTEGER      NOT NULL,
    location_id         SMALLINT     NOT NULL,
    shelf               VARCHAR(10)  NOT NULL,
    bin                 SMALLINT     NOT NULL,
    quantity            SMALLINT     NOT NULL
);
COMMENT ON TABLE production.product_inventory IS 'Current stock per product and location.';

-- ───────────────────────────── purchasing ─────────────────────────────
CREATE TABLE purchasing.ship_method (
    ship_method_id      INTEGER       NOT NULL,
    name                VARCHAR(50)   NOT NULL,
    ship_base           DECIMAL(19,4) NOT NULL,
    ship_rate           DECIMAL(19,4) NOT NULL
);
COMMENT ON TABLE purchasing.ship_method IS 'Shipping carriers.';

CREATE TABLE purchasing.vendor (
    business_entity_id      INTEGER     NOT NULL,
    account_number          VARCHAR(15) NOT NULL,
    name                    VARCHAR(60) NOT NULL,
    credit_rating           SMALLINT    NOT NULL,
    preferred_vendor_status BOOLEAN     NOT NULL,
    active_flag             BOOLEAN     NOT NULL
);
COMMENT ON TABLE purchasing.vendor IS 'Suppliers. credit_rating 1 superior to 5 below average.';

CREATE TABLE purchasing.product_vendor (
    product_id          INTEGER       NOT NULL,
    business_entity_id  INTEGER       NOT NULL,
    average_lead_time   INTEGER       NOT NULL,
    standard_price      DECIMAL(19,4) NOT NULL,
    min_order_qty       INTEGER       NOT NULL,
    max_order_qty       INTEGER       NOT NULL
);
COMMENT ON TABLE purchasing.product_vendor IS 'Which vendors supply which purchased parts.';

CREATE TABLE purchasing.purchase_order_header (
    purchase_order_id   INTEGER       NOT NULL,
    revision_number     SMALLINT      NOT NULL,
    status              SMALLINT      NOT NULL,
    employee_id         INTEGER       NOT NULL,
    vendor_id           INTEGER       NOT NULL,
    ship_method_id      INTEGER       NOT NULL,
    order_date          TIMESTAMP     NOT NULL,
    ship_date           TIMESTAMP,
    sub_total           DECIMAL(19,4) NOT NULL,
    tax_amt             DECIMAL(19,4) NOT NULL,
    freight             DECIMAL(19,4) NOT NULL,
    total_due           DECIMAL(19,4) NOT NULL
);
COMMENT ON TABLE purchasing.purchase_order_header IS 'Purchase orders. status 1 pending, 2 approved, 3 rejected, 4 complete.';

CREATE TABLE purchasing.purchase_order_detail (
    purchase_order_id        INTEGER       NOT NULL,
    purchase_order_detail_id INTEGER       NOT NULL,
    due_date                 TIMESTAMP     NOT NULL,
    order_qty                SMALLINT      NOT NULL,
    product_id               INTEGER       NOT NULL,
    unit_price               DECIMAL(19,4) NOT NULL,
    line_total               DECIMAL(19,4) NOT NULL,
    received_qty             DECIMAL(8,2)  NOT NULL,
    rejected_qty             DECIMAL(8,2)  NOT NULL,
    stocked_qty              DECIMAL(9,2)  NOT NULL
);
COMMENT ON TABLE purchasing.purchase_order_detail IS 'Purchase order lines.';

-- ─────────────────────────────── sales ────────────────────────────────
CREATE TABLE sales.sales_territory (
    territory_id        INTEGER       NOT NULL,
    name                VARCHAR(50)   NOT NULL,
    country_region_code VARCHAR(3)    NOT NULL,
    "group"             VARCHAR(50)   NOT NULL
);
COMMENT ON TABLE sales.sales_territory IS 'Sales regions. Order volume is heavily skewed toward a few territories.';

CREATE TABLE sales.sales_person (
    business_entity_id  INTEGER       NOT NULL,
    territory_id        INTEGER,
    sales_quota         DECIMAL(19,4),
    bonus               DECIMAL(19,4) NOT NULL,
    commission_pct      DECIMAL(10,4) NOT NULL
);
COMMENT ON TABLE sales.sales_person IS 'Sales representatives (subset of employees). Some have no territory or quota.';

CREATE TABLE sales.store (
    business_entity_id  INTEGER       NOT NULL,
    name                VARCHAR(60)   NOT NULL,
    sales_person_id     INTEGER       NOT NULL,
    address_id          INTEGER       NOT NULL
);
COMMENT ON TABLE sales.store IS 'Reseller stores. Their orders are offline, large, and handled by a sales person.';

CREATE TABLE sales.customer (
    customer_id         INTEGER       NOT NULL,
    person_id           INTEGER,
    store_id            INTEGER,
    territory_id        INTEGER       NOT NULL,
    account_number      VARCHAR(10)   NOT NULL,
    modified_date       TIMESTAMP     NOT NULL
);
COMMENT ON TABLE sales.customer IS 'Customers: exactly one of person_id (individual) or store_id (reseller) is set.';

CREATE TABLE sales.special_offer (
    special_offer_id    INTEGER       NOT NULL,
    description         VARCHAR(255)  NOT NULL,
    discount_pct        DECIMAL(10,4) NOT NULL,
    type                VARCHAR(50)   NOT NULL,
    category            VARCHAR(50)   NOT NULL,
    start_date          DATE          NOT NULL,
    end_date            DATE          NOT NULL,
    min_qty             INTEGER       NOT NULL,
    max_qty             INTEGER
);
COMMENT ON TABLE sales.special_offer IS 'Discounts. special_offer_id 1 means no discount.';

CREATE TABLE sales.currency (
    currency_code       VARCHAR(3)    NOT NULL,
    name                VARCHAR(50)   NOT NULL
);
COMMENT ON TABLE sales.currency IS 'Currencies.';

CREATE TABLE sales.currency_rate (
    currency_rate_id    INTEGER       NOT NULL,
    currency_rate_date  DATE          NOT NULL,
    from_currency_code  VARCHAR(3)    NOT NULL,
    to_currency_code    VARCHAR(3)    NOT NULL,
    average_rate        DECIMAL(19,6) NOT NULL,
    end_of_day_rate     DECIMAL(19,6) NOT NULL
);
COMMENT ON TABLE sales.currency_rate IS 'Daily USD exchange rates. Weekends are missing, so use an as-of join.';

CREATE TABLE sales.sales_order_header (
    sales_order_id        INTEGER       NOT NULL,
    revision_number       SMALLINT      NOT NULL,
    order_date            TIMESTAMP     NOT NULL,
    due_date              TIMESTAMP     NOT NULL,
    ship_date             TIMESTAMP,
    status                SMALLINT      NOT NULL,
    online_order_flag     BOOLEAN       NOT NULL,
    sales_order_number    VARCHAR(25)   NOT NULL,
    purchase_order_number VARCHAR(25),
    account_number        VARCHAR(15)   NOT NULL,
    customer_id           INTEGER       NOT NULL,
    sales_person_id       INTEGER,
    territory_id          INTEGER       NOT NULL,
    bill_to_address_id    INTEGER       NOT NULL,
    ship_to_address_id    INTEGER       NOT NULL,
    ship_method_id        INTEGER       NOT NULL,
    currency_rate_id      INTEGER,
    sub_total             DECIMAL(19,4) NOT NULL,
    tax_amt               DECIMAL(19,4) NOT NULL,
    freight               DECIMAL(19,4) NOT NULL,
    total_due             DECIMAL(19,4) NOT NULL
);
COMMENT ON TABLE sales.sales_order_header IS 'Orders. status 1-4 in progress, 5 shipped, 6 cancelled. sub_total equals the sum of detail line_total.';

CREATE TABLE sales.sales_order_detail (
    sales_order_id        INTEGER       NOT NULL,
    sales_order_detail_id BIGINT        NOT NULL,
    carrier_tracking_number VARCHAR(25),
    order_qty             SMALLINT      NOT NULL,
    product_id            INTEGER       NOT NULL,
    special_offer_id      INTEGER       NOT NULL,
    unit_price            DECIMAL(19,4) NOT NULL,
    unit_price_discount   DECIMAL(19,4) NOT NULL,
    line_total            DECIMAL(19,4) NOT NULL
);
COMMENT ON TABLE sales.sales_order_detail IS 'Order lines, the largest table. Product popularity follows a power law.';
