-- Esquema da base de dados do projeto Smart Lockers (PostgreSQL / Neon)
-- Correr uma vez numa base de dados vazia (ex.: SQL Editor da Neon)

CREATE TABLE cacifos (
    id SERIAL PRIMARY KEY,
    ocupado BOOLEAN NOT NULL DEFAULT FALSE,
    porta_aberta BOOLEAN NOT NULL DEFAULT FALSE,
    trancado BOOLEAN NOT NULL DEFAULT TRUE,
    tem_encomenda BOOLEAN NOT NULL DEFAULT FALSE,
    alerta BOOLEAN NOT NULL DEFAULT FALSE,
    dev_eui VARCHAR(16) UNIQUE,
    ultima_comunicacao TIMESTAMP NULL
);

CREATE TABLE pedidos (
    id SERIAL PRIMARY KEY,
    aluno VARCHAR(100) NOT NULL,
    material TEXT NOT NULL,
    estado VARCHAR(20) NOT NULL DEFAULT 'Pendente',
    cacifo_id INTEGER REFERENCES cacifos(id),
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Cacifos de exemplo (o 1 é o XIAO em ABP)
INSERT INTO cacifos (dev_eui) VALUES ('70B3D57ED0079EC6');
INSERT INTO cacifos (dev_eui) VALUES (NULL), (NULL);

-- Um pedido de teste
INSERT INTO pedidos (aluno, material) VALUES ('Aluno Teste', 'Portátil');
