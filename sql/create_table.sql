DROP DATABASE IF EXISTS travel_rag;
CREATE DATABASE travel_rag CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE travel_rag;

CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增，唯一标识用户',
    username VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '用户名',
    phone VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '手机号',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    UNIQUE KEY unique_username (username),
    UNIQUE KEY unique_phone (phone)
) COMMENT='用户表';

CREATE TABLE user_preferences (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增，唯一标识偏好记录',
    user_id INT NOT NULL COMMENT '用户ID',
    home_city VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '常住地城市，仅用于追问候选，不做自动补全',
    transport_preference ENUM('train', 'flight', 'balanced') NOT NULL DEFAULT 'balanced' COMMENT '交通偏好',
    seat_preference VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '高铁席位偏好',
    cabin_preference VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '机票舱位偏好',
    budget_level ENUM('low', 'medium', 'high') NOT NULL DEFAULT 'medium' COMMENT '预算偏好',
    prefer_direct BOOLEAN NOT NULL DEFAULT TRUE COMMENT '是否偏好直达',
    prefer_morning_departure BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否偏好上午出发',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY unique_user_preferences (user_id),
    CONSTRAINT fk_user_preferences_user FOREIGN KEY (user_id) REFERENCES users(id)
) COMMENT='用户偏好画像表';

CREATE TABLE train_tickets (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增，唯一标识每条记录',
    departure_city VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '出发城市（如“北京”）',
    arrival_city VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '到达城市（如“上海”）',
    departure_time DATETIME NOT NULL COMMENT '出发时间（如“2025-08-12 07:00:00”）',
    arrival_time DATETIME NOT NULL COMMENT '到达时间（如“2025-08-12 11:30:00”）',
    train_number VARCHAR(20) NOT NULL COMMENT '火车车次（如“G1001”）',
    seat_type VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '座位类型（如“二等座”）',
    total_seats INT NOT NULL COMMENT '总座位数（如 1000）',
    remaining_seats INT NOT NULL COMMENT '剩余座位数（如 50）',
    price DECIMAL(10, 2) NOT NULL COMMENT '票价（如 553.50）',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间，自动记录插入时间',
    UNIQUE KEY unique_train (departure_time, train_number) -- 唯一约束，确保同一时间和车次不重复
) COMMENT='火车票信息表';

CREATE TABLE flight_tickets (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增，唯一标识每条记录',
    departure_city VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '出发城市（如“北京”）',
    arrival_city VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '到达城市（如“上海”）',
    departure_time DATETIME NOT NULL COMMENT '出发时间（如“2025-08-12 08:00:00”）',
    arrival_time DATETIME NOT NULL COMMENT '到达时间（如“2025-08-12 10:30:00”）',
    flight_number VARCHAR(20) NOT NULL COMMENT '航班号（如“CA1234”）',
    cabin_type VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '舱位类型（如“经济舱”）',
    total_seats INT NOT NULL COMMENT '总座位数（如 200）',
    remaining_seats INT NOT NULL COMMENT '剩余座位数（如 10）',
    price DECIMAL(10, 2) NOT NULL COMMENT '票价（如 1200.00）',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间，自动记录插入时间',
    UNIQUE KEY unique_flight (departure_time, flight_number) -- 唯一约束，确保同一时间和航班号不重复
) COMMENT='航班机票信息表';

CREATE TABLE hotels (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增，唯一标识酒店',
    name VARCHAR(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '酒店名称',
    city VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '所在城市',
    district VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '所在区域',
    star_rating INT NOT NULL COMMENT '酒店星级',
    address VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '酒店地址',
    description VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '酒店简介',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    UNIQUE KEY unique_hotel_city_name (city, name)
) COMMENT='酒店基础信息表';

CREATE TABLE hotel_room_inventory (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增，唯一标识房型库存记录',
    hotel_id INT NOT NULL COMMENT '酒店ID',
    stay_date DATE NOT NULL COMMENT '入住日期',
    room_type VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '房型',
    bed_type VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '床型',
    breakfast_included BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否含早',
    is_refundable BOOLEAN NOT NULL DEFAULT TRUE COMMENT '是否可退',
    total_rooms INT NOT NULL COMMENT '总房量',
    remaining_rooms INT NOT NULL COMMENT '剩余房量',
    price_per_night DECIMAL(10, 2) NOT NULL COMMENT '每晚单价',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    UNIQUE KEY unique_hotel_inventory (hotel_id, stay_date, room_type),
    CONSTRAINT fk_hotel_inventory_hotel FOREIGN KEY (hotel_id) REFERENCES hotels(id)
) COMMENT='酒店房型库存表';

CREATE TABLE orders (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键，自增，唯一标识订单',
    user_id INT NOT NULL COMMENT '用户ID',
    order_type ENUM('train', 'flight', 'hotel') NOT NULL COMMENT '订单类型',
    status ENUM('booked', 'changed', 'cancelled') NOT NULL DEFAULT 'booked' COMMENT '订单状态',
    departure_city VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '出发城市',
    arrival_city VARCHAR(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '到达城市',
    departure_time DATETIME NOT NULL COMMENT '出发时间',
    ticket_or_room_type VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '座位/舱位类型',
    transport_no VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '车次/航班号',
    hotel_name VARCHAR(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '酒店名称，仅 hotel 订单使用',
    stay_nights INT DEFAULT NULL COMMENT '入住晚数，仅 hotel 订单使用',
    quantity INT NOT NULL COMMENT '数量',
    unit_price DECIMAL(10, 2) NOT NULL COMMENT '单价',
    total_price DECIMAL(10, 2) NOT NULL COMMENT '总价',
    raw_order_payload JSON DEFAULT NULL COMMENT '落库原始订单载荷',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_orders_user_status (user_id, status),
    INDEX idx_orders_trip (user_id, order_type, departure_time, transport_no),
    CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users(id)
) COMMENT='订单表';

DROP TABLE IF EXISTS weather_data;
CREATE TABLE IF NOT EXISTS weather_data (
    id INT AUTO_INCREMENT PRIMARY KEY,
    city VARCHAR(50) NOT NULL COMMENT '城市名称',
    fx_date DATE NOT NULL COMMENT '预报日期',
    sunrise TIME COMMENT '日出时间',
    sunset TIME COMMENT '日落时间',
    moonrise TIME COMMENT '月升时间',
    moonset TIME COMMENT '月落时间',
    moon_phase VARCHAR(20) COMMENT '月相名称',
    moon_phase_icon VARCHAR(10) COMMENT '月相图标代码',
    temp_max INT COMMENT '最高温度',
    temp_min INT COMMENT '最低温度',
    icon_day VARCHAR(10) COMMENT '白天天气图标代码',
    text_day VARCHAR(20) COMMENT '白天天气描述',
    icon_night VARCHAR(10) COMMENT '夜间天气图标代码',
    text_night VARCHAR(20) COMMENT '夜间天气描述',
    wind360_day INT COMMENT '白天风向360角度',
    wind_dir_day VARCHAR(20) COMMENT '白天风向',
    wind_scale_day VARCHAR(10) COMMENT '白天风力等级',
    wind_speed_day INT COMMENT '白天风速 (km/h)',
    wind360_night INT COMMENT '夜间风向360角度',
    wind_dir_night VARCHAR(20) COMMENT '夜间风向',
    wind_scale_night VARCHAR(10) COMMENT '夜间风力等级',
    wind_speed_night INT COMMENT '夜间风速 (km/h)',
    precip DECIMAL(5,1) COMMENT '降水量 (mm)',
    uv_index INT COMMENT '紫外线指数',
    humidity INT COMMENT '相对湿度 (%)',
    pressure INT COMMENT '大气压强 (hPa)',
    vis INT COMMENT '能见度 (km)',
    cloud INT COMMENT '云量 (%)',
    update_time DATETIME COMMENT '数据更新时间',
    UNIQUE KEY unique_city_date (city, fx_date)
) ENGINE=INNODB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='天气数据表';
