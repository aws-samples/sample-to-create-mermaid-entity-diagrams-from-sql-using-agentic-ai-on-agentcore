-- HR management schema with multiple relationship types

CREATE TABLE departments (
    id INT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    budget DECIMAL(12,2),
    location VARCHAR(100)
);

CREATE TABLE employees (
    id INT PRIMARY KEY,
    first_name VARCHAR(50) NOT NULL,
    last_name VARCHAR(50) NOT NULL,
    email VARCHAR(255) UNIQUE,
    hire_date DATE NOT NULL,
    salary DECIMAL(10,2),
    department_id INT,
    manager_id INT,
    FOREIGN KEY (department_id) REFERENCES departments(id),
    FOREIGN KEY (manager_id) REFERENCES employees(id)
);

CREATE TABLE projects (
    id INT PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    start_date DATE,
    end_date DATE,
    budget DECIMAL(12,2),
    department_id INT NOT NULL,
    FOREIGN KEY (department_id) REFERENCES departments(id)
);

CREATE TABLE project_assignments (
    id INT PRIMARY KEY,
    employee_id INT NOT NULL,
    project_id INT NOT NULL,
    role VARCHAR(50),
    hours_allocated INT,
    FOREIGN KEY (employee_id) REFERENCES employees(id),
    FOREIGN KEY (project_id) REFERENCES projects(id),
    UNIQUE (employee_id, project_id)
);

CREATE TABLE timesheets (
    id INT PRIMARY KEY,
    employee_id INT NOT NULL,
    project_id INT NOT NULL,
    work_date DATE NOT NULL,
    hours_worked DECIMAL(4,2),
    description TEXT,
    FOREIGN KEY (employee_id) REFERENCES employees(id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
