USE [ODS_DEV]
GO

/****** Object:  View [dbo].[V_Dim_Student]    Script Date: 10/7/2026 12:31:03 PM ******/
SET ANSI_NULLS ON
GO

SET QUOTED_IDENTIFIER ON
GO




















CREATE OR ALTER                                 VIEW [dbo].[V_Dim_Student]
AS
/*************************************************************************************************
    * Script Name: V_Dim_Student
    * Last Modified: 2026-05-18
    *
        Modified    : 2026-05-19  — Performance rewrite v2:
                              1. basePop pre-filtered to enrolled students only
                                 (eliminates need for final enrollment JOIN)
                              2. class_calc replaced with latest_shrtgpa + final_class
                                 (avoids full 3.4M shrtgpa scan; also fixes logic
                                  bug where COUNT(*) DESC returned most historically
                                  frequent class instead of current class)
                              3. online_aggregates now starts from enrollment set
                              4. OUTER APPLY replaced with ROW_NUMBER throughout
                              5. Double correlated subquery for academic standing
                                 replaced with pre-aggregated CTE
                              6. sfbetrm_term_code added to all join keys to
                                 prevent row multiplication


      2026/07/24 Bug - FirstGen needs to be fixed source table changed - maybe in RCVEDEA
	 * Last Modified: 2026-07-29
	 * Last Modified: 2026-09-30	Adult Learner Data Cleanup and Validation
									* For term_date and matric_date spring benchmark date change from March 15 -> March 1
									* ADULT_LEARNER_USG_IND - threshold change from 23 -> 25
									* Rename AGE_USG to CURRENT_AGE

	 * Last Modified: 2026-10-01	FIRSTGEN_UNG_IND logic change
									* Admissions comes first. Bachelor+ = N. Less than HS/HS/some college/certificate/associate = Y. 
									* If Admissions cannot determine it, then look to FAFSA. FAFSA can only give us Y; otherwise the result remains U

									Fg_fa logic change
									* Using Banner_RCVEDEA instead of Banner_RCRAPP3
									* Use the applicable aid year for the student’s current term and the latest RCVEDEA transaction (RCVEDEA_SEQ_NO DESC) for that PIDM/aid year. 
									* RCVEDEA_PARENT_ATTEND_COLLEGE of 1 or 2 = Y. Anything else (3, 4, NULL/no record) = U.

*************************************************************************************************/

WITH current_terms as (
SELECT * from [ODS_DEV].[dbo].[V_Current_Terms]
),
current_students as (
    SELECT  DISTINCT SFRSTCR_PIDM,SFRSTCR_TERM_CODE
    FROM    ODS_DEV.dbo.Banner_SFRSTCR s
            JOIN ODS_DEV.dbo.Banner_STVRSTS v ON 
                v.STVRSTS_CODE=s.SFRSTCR_RSTS_CODE and 
                v.STVRSTS_INCL_SECT_ENRL='Y'
    WHERE   SFRSTCR_TERM_CODE in (SELECT STVTERM_CODE FROM current_terms)
),
base_pop as (
SELECT	*
FROM (
SELECT  cs.SFRSTCR_PIDM,
        iden.SPRIDEN_ID,
        cs.SFRSTCR_TERM_CODE,
        pers.SPBPERS_BIRTH_DATE,
		stdn.SGBSTDN_STYP_CODE,
        stdn.SGBSTDN_LEVL_CODE,
        stdn.SGBSTDN_DEGC_CODE_1,
        deg.STVDEGC_DLEV_CODE,
		stdn.SGBSTDN_MAJR_CODE_1,
        stdn.SGBSTDN_CAMP_CODE,
        stdn.SGBSTDN_RESD_CODE,
		stdn.SGBSTDN_ASTD_CODE,
		stdn.SGBSTDN_TERM_CODE_MATRIC,
		stdn.SGBSTDN_EXP_GRAD_DATE,
        TRY_CONVERT(
            date,
            CONCAT(SUBSTRING(cs.SFRSTCR_TERM_CODE,1,4), 
            '-',
            CASE SUBSTRING(cs.SFRSTCR_TERM_CODE,5,2) 
                WHEN '05' THEN '07-15'
                WHEN '08' THEN '10-15'
                WHEN '02' THEN '03-01' END) -- March 15 -> March 1
        ) AS term_date,
        TRY_CONVERT(
            date,
            CONCAT(SUBSTRING(stdn.SGBSTDN_TERM_CODE_MATRIC,1,4), 
            '-',
            CASE SUBSTRING(stdn.SGBSTDN_TERM_CODE_MATRIC,5,2)
                WHEN '05' THEN '07-15'
                WHEN '08' THEN '10-15'
                WHEN '02' THEN '03-01' -- March 15 -> March 1
            END)
        ) as MATRIC_DATE,
        ROW_NUMBER() OVER (
            PARTITION BY SFRSTCR_PIDM,SFRSTCR_TERM_CODE
            ORDER BY SGBSTDN_TERM_CODE_EFF desc) rn
FROM    current_students cs
        JOIN ODS_DEV.dbo.Banner_SPRIDEN iden ON cs.SFRSTCR_PIDM=iden.SPRIDEN_PIDM and iden.SPRIDEN_CHANGE_IND is null
        JOIN ODS_DEV.dbo.Banner_SPBPERS pers on pers.SPBPERS_PIDM=cs.SFRSTCR_PIDM
        LEFT JOIN ODS_DEV.dbo.Banner_SGBSTDN stdn ON 
            cs.SFRSTCR_PIDM=stdn.SGBSTDN_PIDM and 
            cs.SFRSTCR_TERM_CODE>=stdn.SGBSTDN_TERM_CODE_EFF 
        LEFT JOIN ODS_DEV.dbo.banner_stvdegc deg ON deg.stvdegc_code=stdn.sgbstdn_degc_code_1
) t
WHERE t.rn=1
)
SELECT  b.SFRSTCR_PIDM as PIDM,
		b.SPRIDEN_ID as UNG_ID,
        b.SFRSTCR_TERM_CODE as TERM_CODE,
		CASE WHEN act.rotc_ind=1 THEN 'Cadet'
				WHEN b.sgbstdn_styp_code='D' THEN 'Dual Enrollment'
				WHEN b.sgbstdn_levl_code='GS' THEN 'Graduate'
				WHEN b.sgbstdn_degc_code_1='000000' OR b.stvdegc_dlev_code = 'B' THEN 'Bachelor-Civilian'
				WHEN b.sgbstdn_degc_code_1 in ('999999','CER0','CER1') OR b.stvdegc_dlev_code in ('A','V') THEN 'Associate' 
		END AS STUDENT_SEGMENT,
		b.sgbstdn_styp_code as STUDENT_TYPE_CODE,
		cl.SGRCLSR_CLAS_CODE,
		b.SGBSTDN_MAJR_CODE_1,
		slvl.STUDENT_LEVEL_NUMBER,
		b.sgbstdn_levl_code as DEGREE_LEVEL,
		CASE WHEN oc.online_count = 0 THEN 'Not Online'
			WHEN oc.online_count = oc.total_enrolled THEN 'Only Online'
			ELSE 'Partially Online'
		END AS ONLINE_STATUS,
		CASE WHEN oc.online_non_wd = 0 THEN 'Not Online'
			WHEN oc.online_non_wd = oc.total_enrolled THEN 'Only Online'
			ELSE 'Partially Online'
		END AS ONLINE_STATUS_NON_WD,
		CASE WHEN b.stvdegc_dlev_code NOT IN ('N','X') 
			THEN 'Y'
			ELSE 'N'
		END AS DEGREE_SEEKING_IND,
		CASE WHEN b.sgbstdn_astd_code IS NOT NULL
			THEN b.sgbstdn_astd_code
			ELSE astd.shrttrm_astd_code_end_of_term
		END AS ACADEMIC_STANDING_CODE,
		CASE WHEN hs.SLRRASG_PIDM IS NOT NULL THEN 'DORM' ELSE 'COMM' END AS HOUSING_STATUS,
		b.sgbstdn_camp_code as HOME_CAMPUS_CODE,
		CAST(DATEDIFF(DAY, b.spbpers_birth_date,b.term_date) / 365.25 as int) as CURRENT_AGE, -- Rename from AGE_USG to CURRENT_AGE
		CASE 
			WHEN CAST(DATEDIFF(DAY,b.spbpers_birth_date,b.term_date) / 365.25 as int) >= 23
			THEN 'Y' ELSE 'N'
		END AS ADULT_LEARNER_IND, -- Rename from ADULT_LEARNER_UNG_IND -> ADULT_LEARNER_IND
		CASE 
			WHEN CAST(DATEDIFF(DAY,b.spbpers_birth_date,b.matric_date) / 365.25 as int) >= 25 -- Threshold changed from 23 -> 25
			THEN 'Y' ELSE 'N'
		END AS ADULT_LEARNER_USG_IND,

		-- Old logic

		--CASE WHEN fg_fa.firstgen_fa <> 'Y'
		--	THEN CASE WHEN fg_adm.has_lh_hs = 1 AND fg_adm.has_degree = 0 THEN 'Y'
		--				WHEN fg_adm.has_degree = 1 THEN 'N'
		--				WHEN fg_adm.has_any_parent_record = 0 THEN 'U'
		--				ELSE 'U'
		--			END
		--	ELSE 'Y'
		--END AS FIRSTGEN_UNG_IND,

		CASE
			WHEN fg_adm.has_degree = 1 THEN 'N'
			WHEN fg_adm.has_lh_hs = 1
			  OR fg_adm.went_to_college = 1 THEN 'Y'
			WHEN fg_fa.firstgen_fa = 'Y' THEN 'Y'
			ELSE 'U'
		END AS FIRSTGEN_UNG_IND,

		CASE WHEN fg_adm.has_lh_hs = 1 AND fg_adm.has_degree = 0 AND fg_adm.went_to_college = 0 THEN 'Y'
			WHEN fg_adm.has_degree = 1 THEN 'N'
			WHEN fg_adm.went_to_college = 1 THEN 'N'
			WHEN fg_adm.has_any_parent_record = 0 THEN 'U'
			ELSE 'U'
		END AS FIRSTGEN_USG_IND,
		b.sgbstdn_resd_code AS RESIDENCY_STATUS_CODE,
		CASE WHEN act.rotc_ind = 1 THEN 'Y' ELSE 'N' END AS ROTC_IND,
		CASE WHEN act.honors_ind = 1 THEN 'Y' ELSE 'N' END AS HONORS_IND,
		CASE WHEN act.athlete_ind = 1 THEN 'Y' ELSE 'N' END AS ATHLETE_IND,
		CASE WHEN act.greek_ind = 1 THEN 'Y' ELSE 'N' END AS GREEK_IND,
		CASE WHEN act.mil_vet = 1 THEN 'Y' ELSE 'N' END AS MIL_VETERAN_IND,
		CASE WHEN act.mil_dep = 1 THEN 'Y' ELSE 'N' END AS MIL_DEPSPO_IND,
		CASE WHEN act.path_ind = 1 THEN 'Y' ELSE 'N' END AS PATH_IND,
		CASE WHEN act.ppcp_ind = 1 THEN 'Y' ELSE 'N' END AS PPCP_IND,
		CASE WHEN act.clpp_ind = 1 THEN 'Y' ELSE 'N' END AS CLPP_IND,
		b.sgbstdn_exp_grad_date as EXP_GRAD_DATE
FROM    base_pop b
		LEFT JOIN
			(
				SELECT	DISTINCT
						h.SLRRASG_PIDM,
						h.SLRRASG_TERM_CODE
				FROM	ODS_DEV.dbo.Banner_SLRRASG h
						JOIN ODS_DEV.dbo.Banner_STVASCD stv ON
							stv.STVASCD_CODE=h.SLRRASG_ASCD_CODE AND
							stv.STVASCD_COUNT_IN_USAGE='Y'
				WHERE	h.SLRRASG_BLDG_CODE IS NOT NULL and 
						GETDATE() BETWEEN h.SLRRASG_BEGIN_DATE and h.SLRRASG_END_DATE
			) hs ON hs.SLRRASG_PIDM=b.SFRSTCR_PIDM and hs.SLRRASG_TERM_CODE=b.SFRSTCR_TERM_CODE

    LEFT JOIN ODS_DEV.dbo.Banner_SHRLGPA g ON
        g.SHRLGPA_PIDM=b.SFRSTCR_PIDM and 
		g.SHRLGPA_LEVL_CODE=b.SGBSTDN_LEVL_CODE and
        g.SHRLGPA_GPA_TYPE_IND='O'
    LEFT JOIN ODS_DEV.dbo.Banner_SGRCLSR cl ON
		cl.SGRCLSR_LEVL_CODE=b.SGBSTDN_LEVL_CODE and 
        isnull(g.SHRLGPA_HOURS_EARNED,0) between cl.SGRCLSR_FROM_HOURS and cl.SGRCLSR_TO_HOURS

   OUTER APPLY (
		SELECT	TOP 1 AA.shrttrm_astd_code_end_of_term
        FROM	ODS_DEV.dbo.banner_shrttrm AA
        WHERE	AA.shrttrm_pidm=b.SFRSTCR_PIDM and 
				AA.shrttrm_term_code<=b.SFRSTCR_TERM_CODE and 
				AA.shrttrm_astd_code_end_of_term IS NOT NULL 
		ORDER BY AA.shrttrm_term_code DESC
    ) astd

    OUTER APPLY
    (
	    SELECT	COUNT(*) AS TOTAL_ENROLLED,
			    SUM(CASE WHEN sect.SSBSECT_INSM_CODE IN ('E','F') THEN 1 ELSE 0 END) as ONLINE_COUNT,
			    SUM(CASE WHEN sect.SSBSECT_INSM_CODE IN ('E','F') and v.STVRSTS_WITHDRAW_IND='N' THEN 1 ELSE 0 END) as ONLINE_NON_WD
	    FROM	ODS_DEV.dbo.Banner_SFRSTCR r
			    JOIN ODS_DEV.dbo.Banner_STVRSTS v ON
				    v.STVRSTS_CODE=r.SFRSTCR_RSTS_CODE and v.STVRSTS_INCL_SECT_ENRL='Y'
			    LEFT JOIN ODS_DEV.dbo.Banner_SSBSECT sect ON
				    sect.SSBSECT_TERM_CODE=r.SFRSTCR_TERM_CODE and sect.SSBSECT_CRN = r.SFRSTCR_CRN
	    WHERE	r.SFRSTCR_PIDM=b.SFRSTCR_PIDM and r.SFRSTCR_TERM_CODE=b.SFRSTCR_TERM_CODE
    ) oc

    OUTER APPLY
    (
	    SELECT	MAX(CASE WHEN activity.ACTIVITY = 'HNRS' THEN 1 ELSE 0 END) AS HONORS_IND,
			    MAX(CASE WHEN activity.ACTIVITY = 'ATH' THEN 1 ELSE 0 END) AS ATHLETE_IND,
			    MAX(CASE WHEN activity.ACTIVITY = 'Greek' THEN 1 ELSE 0 END) AS GREEK_IND,
			    MAX(CASE WHEN activity.ACTIVITY = 'MIL_VET' THEN 1 ELSE 0 END) AS MIL_VET,
			    MAX(CASE WHEN activity.ACTIVITY = 'MIL_DEP' THEN 1 ELSE 0 END) AS MIL_DEP,
			    MAX(CASE WHEN activity.ACTIVITY = 'ROTC' THEN 1 ELSE 0 END) AS ROTC_IND,
			    MAX(CASE WHEN activity.ACTIVITY = 'PATH' THEN 1 ELSE 0 END) AS PATH_IND,
			    MAX(CASE WHEN activity.ACTIVITY = 'PPCP' THEN 1 ELSE 0 END) AS PPCP_IND,
			    MAX(CASE WHEN activity.ACTIVITY = 'GROT' THEN 1 ELSE 0 END) AS CLPP_IND
	    FROM
	    (
		    SELECT	CASE WHEN greek.GREEK_ATTS_CODE IS NOT NULL THEN 'Greek'
					     WHEN military.MIL_VETERAN_IND = 'Y' THEN 'MIL_VET'
					     WHEN military.MIL_DEPENDENT_IND = 'Y' THEN 'MIL_DEP'
					     ELSE attr.SGRSATT_ATTS_CODE END AS ACTIVITY
		    FROM	ODS_DEV.dbo.Banner_SGRSATT attr
				    LEFT JOIN ODS_DEV.dbo.Banner_Local_W_Greek_Org greek ON
					    greek.GREEK_ATTS_CODE = attr.SGRSATT_ATTS_CODE
				    LEFT JOIN ODS_DEV.dbo.Banner_Local_W_Military_Attributes military ON
					    military.MIL_ATTS_CODE = attr.SGRSATT_ATTS_CODE
		    WHERE	attr.SGRSATT_PIDM=b.SFRSTCR_PIDM and 
                    attr.SGRSATT_TERM_CODE_EFF=b.SFRSTCR_TERM_CODE and
			        (
				        attr.SGRSATT_ATTS_CODE in ('ROTC','PATH','PPCP','GROT')
				        OR greek.GREEK_ACTIVE_IND = 'Y'
				        OR military.MIL_VETERAN_IND = 'Y'
				        OR military.MIL_DEPENDENT_IND = 'Y'
			        )
		    UNION ALL
		    SELECT	'HNRS' AS ACTIVITY
		    FROM	ODS_DEV.dbo.Banner_SGRSACT act
		    WHERE	act.SGRSACT_PIDM=b.SFRSTCR_PIDM and 
                    act.SGRSACT_TERM_CODE=b.SFRSTCR_TERM_CODE and
			        act.SGRSACT_ACTC_CODE='HNRS'
		    UNION ALL
		    SELECT	'ATH' AS ACTIVITY
		    FROM	ODS_DEV.dbo.Banner_SGRSPRT sport
		    WHERE	sport.SGRSPRT_PIDM=b.SFRSTCR_PIDM and sport.SGRSPRT_TERM_CODE=b.SFRSTCR_TERM_CODE
	    ) activity
    ) act

	OUTER APPLY
	(
		SELECT	MAX(CASE WHEN a.SARAATT_ATTS_CODE IN ('P1BD','P2BD','P1DD','P2DD','P1MD','P2MD') THEN 1 ELSE 0 END) AS HAS_DEGREE,
				MAX(CASE WHEN a.SARAATT_ATTS_CODE IN ('P1LH','P1HS','P2LH','P2HS') THEN 1 ELSE 0 END) AS HAS_LH_HS,
				MAX(CASE WHEN a.SARAATT_ATTS_CODE IN ('P1CC','P2CC','P1AD','P2AD','P1SC','P2SC') THEN 1 ELSE 0 END) AS WENT_TO_COLLEGE,
				MAX(CASE WHEN a.SARAATT_ATTS_CODE IS NOT NULL THEN 1 ELSE 0 END) AS HAS_ANY_PARENT_RECORD
		FROM	ODS_DEV.dbo.Banner_SARAATT a
		WHERE	a.SARAATT_PIDM=b.SFRSTCR_PIDM
	) fg_adm

	OUTER APPLY

	(
		SELECT TOP 1
			CASE 
				WHEN r.RCVEDEA_PARENT_ATTEND_COLLEGE IN ('1','2') THEN 'Y'
				ELSE 'U'
			END AS FIRSTGEN_FA
		FROM ODS_DEV.dbo.Banner_RCVEDEA r
		WHERE r.RCVEDEA_PIDM = b.SFRSTCR_PIDM
		  AND r.RCVEDEA_AIDY_CODE =
			CASE
				-- Fall: 202608 -> 2627
				WHEN RIGHT(b.SFRSTCR_TERM_CODE, 2) = '08'
					THEN RIGHT(LEFT(b.SFRSTCR_TERM_CODE, 4), 2)
					   + RIGHT(CAST(CAST(LEFT(b.SFRSTCR_TERM_CODE, 4) AS INT) + 1 AS VARCHAR(4)), 2)
 
				-- Spring/Summer: 202602/202605 -> 2526
				WHEN RIGHT(b.SFRSTCR_TERM_CODE, 2) IN ('02','05')
					THEN RIGHT(CAST(CAST(LEFT(b.SFRSTCR_TERM_CODE, 4) AS INT) - 1 AS VARCHAR(4)), 2)
					   + RIGHT(LEFT(b.SFRSTCR_TERM_CODE, 4), 2)
			END
		ORDER BY r.RCVEDEA_SEQ_NO DESC
	) fg_fa
	-- Old Logic
	--(
	--	SELECT	CASE
	--				WHEN MAX(r.RCRAPP3_FATHER_HI_GRADE) IN ('1','2') and
	--					 MAX(r.RCRAPP3_MOTHER_HI_GRADE) IN ('1','2') THEN 'Y'
	--				WHEN MAX(r.RCRAPP3_FATHER_HI_GRADE) = '3' or
	--					 MAX(r.RCRAPP3_MOTHER_HI_GRADE) = '3' THEN 'N'
	--				ELSE 'U' END AS FIRSTGEN_FA
	--	FROM	ODS_DEV.dbo.Banner_RCRAPP3 r
	--	WHERE	r.RCRAPP3_PIDM=b.SFRSTCR_PIDM
	--) fg_fa

	LEFT JOIN ODS_DEV.dbo.V_Student_Level_Number_Mapping slvl ON
		slvl.CLASS_CODE = COALESCE(cl.SGRCLSR_CLAS_CODE,'UNKNOWN') and 
		slvl.STUDENT_TYPE_CODE = COALESCE(b.SGBSTDN_STYP_CODE,'UNKNOWN') and 
		slvl.DEGREE_LEVEL_CODE = b.SGBSTDN_LEVL_CODE and 
		slvl.MAJOR_CODE = COALESCE(b.SGBSTDN_MAJR_CODE_1,'UNKNOWN')
		
		
GO


