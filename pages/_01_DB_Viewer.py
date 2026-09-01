import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text
from modules.db_models import (
    Material, Property, MaterialProperty, Article,
    TemperatureProperty, InterfaceProperty, ConstitutiveModel,
    BendingParameter, LoadDeflectionCurve, FatigueProperty
)
import pandas as pd

# ---------- DB SETUP ----------
engine = create_engine("sqlite:///materials_properties.db")
Session = sessionmaker(bind=engine)
session = Session()

st.set_page_config(page_title="📂 Materials & Properties DB", layout="wide")
st.title("📊 Materials & Solar Cell Database Viewer")

def safe_dataframe(df):
    """Display a DataFrame safely, avoiding Streamlit rendering bugs."""
    if df is None or df.empty:
        st.info("No data to display.")
    else:
        # Convert all columns to string to avoid internal rendering issues
        st.dataframe(df.astype(str), use_container_width=True)

# ---------- Table Selection ----------
tables = [
    "materials", "properties", "material_properties", "articles",
    "temperature_properties", "interface_properties", "constitutive_models",
    "bending_parameters", "load_deflection_curves", "fatigue_properties"
]
table_name = st.selectbox("Select Table", tables)

# ---------- MATERIALS (enhanced) ----------
if table_name == "materials":
    materials = session.query(Material).all()
    material_names = [f"{m.english_name} ({m.chinese_name})" for m in materials]
    selected_material = st.selectbox("Select Material", material_names)

    if selected_material:
        english_name = selected_material.split(" (")[0]
        material = session.query(Material).filter_by(english_name=english_name).one()

        # Basic info
        st.subheader("📄 Material Info")
        col1, col2 = st.columns(2)
        with col1:
            st.write("**Chinese Name:**", material.chinese_name)
            st.write("**English Name:**", material.english_name)
            st.write("**Formula:**", material.formula)
        with col2:
            st.write("**Lattice Constant:**", f"{material.lattice_constant} {material.lattice_constant_unit or ''}")
            st.write("**Defect Density:**", f"{material.defect_density} {material.defect_density_unit or ''}")
            st.write("**Elastic Modulus Temp Coeff:**", f"{material.elastic_modulus_temp_coeff} /°C" if material.elastic_modulus_temp_coeff else "—")

        # Expandable sections for different property groups
        with st.expander("🔬 Thermal & Mechanical Properties"):
            thermal_data = {
                "Thermal Conductivity (W/(m·K))": material.thermal_conductivity,
                "Specific Heat (J/(kg·K))": material.specific_heat,
                "CTE (ppm/K)": material.cte,
                "Elastic Modulus (GPa)": material.elastic_modulus,
                "Shear Modulus (GPa)": material.shear_modulus,
                "Bulk Modulus (GPa)": material.bulk_modulus,
                "Poisson Ratio": material.poisson_ratio,
                "Yield Strength (MPa)": material.yield_strength,
                "Fracture Toughness (MPa·√m)": material.fracture_toughness,
            }
            df_thermal = pd.DataFrame([thermal_data]).T.reset_index()
            df_thermal.columns = ["Property", "Value"]
            safe_dataframe(df_thermal)

        with st.expander("💡 Optical & Electrical Properties"):
            opt_elec = {
                "Band Gap (eV)": material.band_gap,
                "Refractive Index": material.refractive_index,
                "Absorption Coefficient (cm⁻¹)": material.absorption_coefficient,
                "Carrier Mobility (cm²/(V·s))": material.carrier_mobility,
                "Dielectric Constant": material.dielectric_constant,
                "Doping Concentration (cm⁻³)": material.doping_concentration,
                "Carrier Lifetime (s)": material.carrier_lifetime,
                "Recombination Coefficient (cm³/s)": material.recombination_coefficient,
            }
            df_opt = pd.DataFrame([opt_elec]).T.reset_index()
            df_opt.columns = ["Property", "Value"]
            safe_dataframe(df_opt)

        with st.expander("🧪 Polymer Specific"):
            polymer = {
                "Glass Transition Temp (°C)": material.glass_transition_temp,
                "Moisture Permeability (g/(m²·day))": material.moisture_permeability,
                "Weather Resistance Rating": material.weather_resistance_rating,
            }
            df_poly = pd.DataFrame([polymer]).T.reset_index()
            df_poly.columns = ["Property", "Value"]
            safe_dataframe(df_poly)

        # Related articles
        st.subheader("📚 Related Articles")
        articles = session.execute(text("""
            SELECT id, title, source, journal, date 
            FROM articles 
            WHERE material_id = :material_id
            ORDER BY date DESC
        """), {"material_id": material.id}).fetchall()
        if articles:
            article_data = [{
                "Article ID": a.id,
                "Title": a.title[:100] + "..." if len(a.title) > 100 else a.title,
                "Source": a.source,
                "Journal": a.journal,
                "Date": a.date
            } for a in articles]
            safe_dataframe(pd.DataFrame(article_data))
        else:
            st.info("No articles linked to this material.")

        # Advanced simulation data
        st.subheader("⚙️ Simulation & Interface Data")
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "Temperature Properties", "Interface Properties", "Constitutive Model",
            "Bending", "Fatigue"
        ])

        with tab1:
            temp_props = session.query(TemperatureProperty).filter_by(material_id=material.id).all()
            if temp_props:
                df_temp = pd.DataFrame([{
                    "Property Type": tp.property_type,
                    "Temperature (°C)": tp.temperature_celsius,
                    "Value": tp.value,
                    "Unit": tp.unit
                } for tp in temp_props])
                safe_dataframe(df_temp)
            else:
                st.info("No temperature‑dependent data.")

        with tab2:
            interfaces = session.query(InterfaceProperty).filter(
                (InterfaceProperty.material1_id == material.id) |
                (InterfaceProperty.material2_id == material.id)
            ).all()
            if interfaces:
                df_iface = []
                for iface in interfaces:
                    other_id = iface.material2_id if iface.material1_id == material.id else iface.material1_id
                    other = session.get(Material, other_id)
                    df_iface.append({
                        "With Material": f"{other.english_name} ({other.chinese_name})",
                        "Property Type": iface.property_type,
                        "Value": iface.value,
                        "Unit": iface.unit,
                        "Temp (°C)": iface.temperature_celsius,
                        "Source": iface.source
                    })
                safe_dataframe(pd.DataFrame(df_iface))
            else:
                st.info("No interface properties recorded.")

        with tab3:
            cm = session.query(ConstitutiveModel).filter_by(material_id=material.id).first()
            if cm:
                st.write(f"**Model Type:** {cm.model_type}")
                st.write(f"**Linear Coefficient (θ₀):** {cm.linear_coefficient}")
                st.write(f"**Exponential Coefficient (θ₁):** {cm.exponential_coefficient}")
                st.write(f"**Saturation Stress (σ_sat):** {cm.saturation_stress}")
                st.write(f"**Initial Yield Stress (σ_y0):** {cm.yield_stress_initial}")
                if cm.temperature_dependence:
                    st.write("**Temperature Dependence:**", cm.temperature_dependence)
            else:
                st.info("No constitutive model defined.")

        with tab4:
            bending = session.query(BendingParameter).filter_by(material_id=material.id).first()
            if bending:
                st.write(f"**Bending Radius:** {bending.bending_radius_mm} mm")
                st.write(f"**Strain at Radius:** {bending.strain_at_radius}")
                st.write(f"**Critical Buckling Load:** {bending.critical_buckling_load_N} N")
                st.write(f"**Deflection @ Load:** {bending.deflection_mm} mm @ {bending.load_N} N")
                st.write(f"**Source:** {bending.source}")
            else:
                st.info("No bending parameters.")

            # Load‑deflection curve points
            load_points = session.query(LoadDeflectionCurve).filter_by(material_id=material.id).all()
            if load_points:
                st.write("**Load‑Deflection Curve Points:**")
                df_points = pd.DataFrame([{
                    "Load (N)": lp.load_N,
                    "Deflection (mm)": lp.deflection_mm,
                    "Source": lp.source
                } for lp in load_points])
                if not df_points.empty:
                    st.line_chart(df_points.set_index("Load (N)"))
                    safe_dataframe(df_points)
            else:
                st.caption("No load‑deflection curve points.")

        with tab5:
            fatigue = session.query(FatigueProperty).filter_by(material_id=material.id).all()
            if fatigue:
                df_fat = []
                for f in fatigue:
                    df_fat.append({
                        "Type": f.property_type,
                        "Stress Amplitude (MPa)": f.stress_amplitude_MPa,
                        "Cycles to Failure": f.cycles_to_failure,
                        "Paris C": f.paris_C,
                        "Paris m": f.paris_m,
                        "da/dN (m/cycle)": f.da_dN_m_per_cycle,
                        "ΔK (MPa√m)": f.delta_K_MPam,
                        "Source": f.source
                    })
                safe_dataframe(pd.DataFrame(df_fat))
            else:
                st.info("No fatigue properties.")

        # Generic properties via material_properties link
        st.subheader("📋 Generic Properties (via material_properties)")
        if material.properties:
            cats = {}
            for mp in material.properties:
                cat = mp.property.category or "Uncategorized"
                cats.setdefault(cat, []).append({
                    "Property (EN)": mp.property.english_name,
                    "Property (CN)": mp.property.chinese_name,
                    "Value": mp.value,
                    "Unit": mp.unit or mp.property.typical_units,
                    "Source": mp.source
                })
            for cat, rows in cats.items():
                with st.expander(f"🔹 {cat}"):
                    safe_dataframe(pd.DataFrame(rows))
        else:
            st.info("No generic properties assigned.")

# ---------- OTHER TABLES (simplified viewers) ----------
elif table_name == "properties":
    props = session.query(Property).all()
    df = pd.DataFrame([{
        "Chinese Name": p.chinese_name,
        "English Name": p.english_name,
        "Symbol": p.symbol,
        "Typical Units": p.typical_units,
        "Category": p.category,
        "Notes": p.notes
    } for p in props])
    safe_dataframe(df)

elif table_name == "material_properties":
    query = text("""
        SELECT 
            m.english_name as material,
            p.english_name as property,
            p.category,
            mp.value,
            mp.unit,
            mp.source,
            a.id as article_id,
            a.title as article_title
        FROM material_properties mp
        JOIN materials m ON mp.material_id = m.id
        JOIN properties p ON mp.property_id = p.id
        LEFT JOIN articles a ON mp.material_id = a.material_id
        ORDER BY m.english_name, p.english_name
    """)
    results = session.execute(query).fetchall()
    df = pd.DataFrame([dict(row._mapping) for row in results])
    safe_dataframe(df)

elif table_name == "articles":
    articles_data = session.execute(text("""
        SELECT a.*, m.english_name as material_name
        FROM articles a
        LEFT JOIN materials m ON a.material_id = m.id
        ORDER BY a.date DESC
    """)).fetchall()
    if articles_data:
        df = pd.DataFrame([dict(row._mapping) for row in articles_data])
        safe_dataframe(df)
    else:
        st.info("No articles.")

# ---------- NEW TABLE VIEWERS ----------
elif table_name == "temperature_properties":
    df = pd.read_sql(session.query(TemperatureProperty).statement, session.bind)
    safe_dataframe(df)

elif table_name == "interface_properties":
    query = text("""
        SELECT 
            ip.*,
            m1.english_name as material1,
            m2.english_name as material2
        FROM interface_properties ip
        JOIN materials m1 ON ip.material1_id = m1.id
        JOIN materials m2 ON ip.material2_id = m2.id
    """)
    df = pd.read_sql(query, session.bind)
    safe_dataframe(df)

elif table_name == "constitutive_models":
    df = pd.read_sql(session.query(ConstitutiveModel).statement, session.bind)
    if not df.empty:
        material_ids = df["material_id"].tolist()
        mats = session.query(Material).filter(Material.id.in_(material_ids)).all()
        mat_dict = {m.id: f"{m.english_name} ({m.chinese_name})" for m in mats}
        df["material"] = df["material_id"].map(mat_dict)
    safe_dataframe(df)

elif table_name == "bending_parameters":
    df = pd.read_sql(session.query(BendingParameter).statement, session.bind)
    if not df.empty:
        mats = session.query(Material).filter(Material.id.in_(df["material_id"])).all()
        mat_dict = {m.id: f"{m.english_name} ({m.chinese_name})" for m in mats}
        df["material"] = df["material_id"].map(mat_dict)
    safe_dataframe(df)

elif table_name == "load_deflection_curves":
    df = pd.read_sql(session.query(LoadDeflectionCurve).statement, session.bind)
    if not df.empty:
        mats = session.query(Material).filter(Material.id.in_(df["material_id"])).all()
        mat_dict = {m.id: f"{m.english_name} ({m.chinese_name})" for m in mats}
        df["material"] = df["material_id"].map(mat_dict)
    safe_dataframe(df)

elif table_name == "fatigue_properties":
    df = pd.read_sql(session.query(FatigueProperty).statement, session.bind)
    if not df.empty:
        mats = session.query(Material).filter(Material.id.in_(df["material_id"])).all()
        mat_dict = {m.id: f"{m.english_name} ({m.chinese_name})" for m in mats}
        df["material"] = df["material_id"].map(mat_dict)
    safe_dataframe(df)

# Close session
session.close()