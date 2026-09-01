from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Float, 
    ForeignKey, Boolean, JSON
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

Base = declarative_base()


class Material(Base):
    """Main material table – stores intrinsic properties of semiconductors, metals, polymers."""
    __tablename__ = "materials"

    id = Column(Integer, primary_key=True, comment="材料ID")
    chinese_name = Column(String, comment="中文名称")
    english_name = Column(String, comment="英文名称")
    formula = Column(String, nullable=True, comment="化学式")

    # Crystal structure
    lattice_constant = Column(Float, nullable=True, comment="晶格常数")
    lattice_constant_unit = Column(String, nullable=True, comment="晶格常数单位")
    defect_density = Column(Float, nullable=True, comment="缺陷密度")
    defect_density_unit = Column(String, nullable=True, comment="缺陷密度单位")

    # Thermal (single value, room temperature)
    thermal_conductivity = Column(Float, nullable=True, comment="热导率 (W/(m·K))")
    specific_heat = Column(Float, nullable=True, comment="比热容 (J/(kg·K))")
    cte = Column(Float, nullable=True, comment="热膨胀系数 (ppm/K)")

    # Mechanical
    elastic_modulus = Column(Float, nullable=True, comment="弹性模量 (GPa)")
    shear_modulus = Column(Float, nullable=True, comment="剪切模量 (GPa)")
    bulk_modulus = Column(Float, nullable=True, comment="体积模量 (GPa)")
    poisson_ratio = Column(Float, nullable=True, comment="泊松比")
    yield_strength = Column(Float, nullable=True, comment="屈服强度 (MPa)")
    fracture_toughness = Column(Float, nullable=True, comment="断裂韧性 (MPa·√m)")

    # Elastic modulus temperature degradation coefficient
    elastic_modulus_temp_coeff = Column(Float, nullable=True, comment="弹性模量温度退化系数 (1/°C 或 %/°C)")

    # Optical / Electrical
    band_gap = Column(Float, nullable=True, comment="禁带宽度 (eV)")
    refractive_index = Column(Float, nullable=True, comment="折射率")
    absorption_coefficient = Column(Float, nullable=True, comment="吸收系数 (cm⁻¹)")
    carrier_mobility = Column(Float, nullable=True, comment="载流子迁移率 (cm²/(V·s))")
    dielectric_constant = Column(Float, nullable=True, comment="介电常数")
    doping_concentration = Column(Float, nullable=True, comment="掺杂浓度 (cm⁻³)")
    carrier_lifetime = Column(Float, nullable=True, comment="载流子寿命 (s)")
    recombination_coefficient = Column(Float, nullable=True, comment="复合系数 (cm³/s)")

    # Polymer specific
    glass_transition_temp = Column(Float, nullable=True, comment="玻璃化转变温度 (°C)")
    moisture_permeability = Column(Float, nullable=True, comment="透湿率 (g/(m²·day))")
    weather_resistance_rating = Column(String, nullable=True, comment="耐候性等级")

    # Relationships
    properties = relationship("MaterialProperty", back_populates="material")
    articles = relationship("Article", back_populates="material")
    temp_properties = relationship("TemperatureProperty", back_populates="material")
    interface_as_material1 = relationship("InterfaceProperty", foreign_keys="InterfaceProperty.material1_id", back_populates="material1")
    interface_as_material2 = relationship("InterfaceProperty", foreign_keys="InterfaceProperty.material2_id", back_populates="material2")
    constitutive_model = relationship("ConstitutiveModel", uselist=False, back_populates="material")
    bending_params = relationship("BendingParameter", uselist=False, back_populates="material")
    load_deflection_points = relationship("LoadDeflectionCurve", back_populates="material")
    fatigue_properties = relationship("FatigueProperty", back_populates="material")


class Property(Base):
    __tablename__ = "properties"
    id = Column(Integer, primary_key=True, comment="属性ID")
    chinese_name = Column(String, comment="中文名称")
    english_name = Column(String, comment="英文名称")
    symbol = Column(String, nullable=True, comment="符号")
    typical_units = Column(String, nullable=True, comment="典型单位")
    notes = Column(Text, nullable=True, comment="备注")
    category = Column(String, comment="类别")

    materials = relationship("MaterialProperty", back_populates="property")


class MaterialProperty(Base):
    __tablename__ = "material_properties"
    id = Column(Integer, primary_key=True, comment="关联ID")
    material_id = Column(Integer, ForeignKey("materials.id"), comment="材料ID")
    property_id = Column(Integer, ForeignKey("properties.id"), comment="属性ID")
    value = Column(Float, nullable=True, comment="数值")
    unit = Column(String, nullable=True, comment="单位")
    source = Column(Text, nullable=True, comment="来源")

    material = relationship("Material", back_populates="properties")
    property = relationship("Property", back_populates="materials")


class Article(Base):
    __tablename__ = "articles"
    id = Column(Integer, primary_key=True, comment="文章ID")
    material_id = Column(Integer, ForeignKey("materials.id"), comment="关联材料ID")
    
    # Core metadata
    title = Column(Text, comment="标题")
    authors = Column(Text, comment="作者")
    source = Column(Text, comment="来源 (arXiv/PubMed)")
    journal = Column(String, nullable=True, comment="期刊")
    date = Column(String, nullable=True, comment="出版日期")
    
    # New columns for identifiers and abstract
    doi = Column(String, nullable=True, comment="DOI")
    link = Column(String, nullable=True, comment="文章链接")
    pmid = Column(String, nullable=True, comment="PubMed ID")
    abstract = Column(Text, nullable=True, comment="摘要")
    
    # Extracted content (from RAG)
    innovations = Column(Text, nullable=True, comment="创新点")
    battery_structure = Column(Text, nullable=True, comment="电池结构")
    fabrication_process = Column(Text, nullable=True, comment="制备工艺")
    
    # Battery performance
    efficiency_percent = Column(Float, nullable=True, comment="效率(%)")
    open_circuit_voltage = Column(Float, nullable=True, comment="开路电压(V)")
    short_circuit_current = Column(Float, nullable=True, comment="短路电流(mA/cm²)")
    fill_factor = Column(Float, nullable=True, comment="填充因子")
    areal_density = Column(Float, nullable=True, comment="面密度(g/m²)")
    specific_power = Column(Float, nullable=True, comment="比功率(W/kg)")
    
    # Flags
    is_solar_cell = Column(Boolean, default=True, comment="太阳电池文献")
    is_gaas = Column(Boolean, default=False, comment="涉及砷化镓")
    is_flexible_thin_film_gaas = Column(Boolean, default=False, comment="柔性薄膜砷化镓")
    is_flexible_substrate = Column(Boolean, default=False, comment="柔性衬底")
    
    # RAG metrics
    recall_rate = Column(Float, nullable=True, comment="召回率")
    matching_degree = Column(Float, nullable=True, comment="匹配度")
    
    material = relationship("Material", back_populates="articles")


class TemperatureProperty(Base):
    __tablename__ = "temperature_properties"
    id = Column(Integer, primary_key=True, comment="ID")
    material_id = Column(Integer, ForeignKey("materials.id"), comment="材料ID")
    property_type = Column(String, comment="属性类型")
    temperature_celsius = Column(Float, comment="温度(°C)")
    value = Column(Float, comment="数值")
    unit = Column(String, nullable=True, comment="单位")
    material = relationship("Material", back_populates="temp_properties")


class InterfaceProperty(Base):
    __tablename__ = "interface_properties"
    id = Column(Integer, primary_key=True, comment="ID")
    material1_id = Column(Integer, ForeignKey("materials.id"), comment="材料1 ID")
    material2_id = Column(Integer, ForeignKey("materials.id"), comment="材料2 ID")
    property_type = Column(String, comment="类型: adhesion/shear_strength/thermal_resistance")
    value = Column(Float, comment="数值")
    unit = Column(String, nullable=True, comment="单位")
    temperature_celsius = Column(Float, nullable=True, comment="温度(°C)")
    source = Column(Text, nullable=True, comment="来源")
    material1 = relationship("Material", foreign_keys=[material1_id], back_populates="interface_as_material1")
    material2 = relationship("Material", foreign_keys=[material2_id], back_populates="interface_as_material2")


class ConstitutiveModel(Base):
    __tablename__ = "constitutive_models"
    id = Column(Integer, primary_key=True, comment="ID")
    material_id = Column(Integer, ForeignKey("materials.id"), unique=True, comment="材料ID")
    model_type = Column(String, default="Voce", comment="模型类型")
    linear_coefficient = Column(Float, comment="线性系数")
    exponential_coefficient = Column(Float, comment="指数系数")
    saturation_stress = Column(Float, comment="饱和应力")
    yield_stress_initial = Column(Float, comment="初始屈服应力")
    temperature_dependence = Column(JSON, nullable=True, comment="温度相关参数")
    material = relationship("Material", back_populates="constitutive_model")


class BendingParameter(Base):
    __tablename__ = "bending_parameters"
    id = Column(Integer, primary_key=True, comment="ID")
    material_id = Column(Integer, ForeignKey("materials.id"), unique=True, comment="材料ID")
    bending_radius_mm = Column(Float, comment="弯曲半径(mm)")
    strain_at_radius = Column(Float, comment="应变")
    critical_buckling_load_N = Column(Float, nullable=True, comment="临界屈曲载荷(N)")
    deflection_mm = Column(Float, comment="挠度(mm)")
    load_N = Column(Float, nullable=True, comment="载荷(N)")
    source = Column(Text, nullable=True, comment="来源")
    material = relationship("Material", back_populates="bending_params")


class LoadDeflectionCurve(Base):
    __tablename__ = "load_deflection_curves"
    id = Column(Integer, primary_key=True, comment="ID")
    material_id = Column(Integer, ForeignKey("materials.id"), comment="材料ID")
    load_N = Column(Float, comment="载荷(N)")
    deflection_mm = Column(Float, comment="挠度(mm)")
    source = Column(Text, nullable=True, comment="来源")
    material = relationship("Material", back_populates="load_deflection_points")


class FatigueProperty(Base):
    __tablename__ = "fatigue_properties"
    id = Column(Integer, primary_key=True, comment="ID")
    material_id = Column(Integer, ForeignKey("materials.id"), comment="材料ID")
    property_type = Column(String, comment="类型")
    stress_amplitude_MPa = Column(Float, nullable=True, comment="应力幅值(MPa)")
    cycles_to_failure = Column(Float, nullable=True, comment="循环次数")
    paris_C = Column(Float, nullable=True, comment="Paris常数C")
    paris_m = Column(Float, nullable=True, comment="Paris指数m")
    da_dN_m_per_cycle = Column(Float, nullable=True, comment="裂纹扩展速率(m/cycle)")
    delta_K_MPam = Column(Float, nullable=True, comment="ΔK (MPa√m)")
    source = Column(Text, nullable=True, comment="来源")
    material = relationship("Material", back_populates="fatigue_properties")