CREATE TABLE IF NOT EXISTS users (
  name VARCHAR(255) NOT NULL,
  api_key CHAR(36) PRIMARY KEY,
  credits INT NOT NULL CHECK (credits >= 0)
);
