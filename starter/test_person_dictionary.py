import unittest
from starter.person_rules import clean_values,decide,COLUMNS
from starter.person_ownership import group_records
from starter.person_dictionary import process_prepared


def person(pid=1,name='N',phone='P',rrn=None):
    return {'pid':pid,**clean_values({'data2':[name] if name else [],'data1':[phone] if phone else [],'data5':[rrn] if rrn else []})}


def mention(text,value,label,token):
    start=text.index(value)
    return {'start':start,'end':start+len(value),'original':value,'normalized':value,'label':label,'token':token}


class RuleTests(unittest.TestCase):
    def test_a_name_only_holds(self):
        self.assertEqual(decide({'data2':['N']},[person()])['reason'],'NAME_ONLY')
    def test_b_name_phone_merges(self):
        self.assertEqual(decide({'data2':['N'],'data1':['P']},[person()])['pid'],1)
    def test_c_changed_phone_holds(self):
        self.assertEqual(decide({'data2':['N'],'data1':['NEW']},[person()])['reason'],'PHONE_DIFFERENT')
    def test_no_candidate_creates(self):
        self.assertEqual(decide({'data2':['NEW']},[person()])['action'],'create')
    def test_multiple_b_matches_holds(self):
        self.assertEqual(decide({'data2':['N'],'data1':['P']},[person(1),person(2)])['reason'],'MULTIPLE_MATCHES')
    def test_rrn_conflict_blocks_b(self):
        self.assertEqual(decide({'data2':['N'],'data1':['P'],'data5':['R2']},[person(rrn='R1')])['reason'],'RRN_CONFLICT')
    def test_other_homonym_does_not_block_unique_b(self):
        self.assertEqual(decide({'data2':['N'],'data1':['P'],'data5':['R1']},[person(rrn='R1'),person(2,phone='P2',rrn='R2')])['pid'],1)
    def test_rrn_unique_with_same_name(self):
        self.assertEqual(decide({'data2':['N'],'data5':['R']},[person(rrn='R')])['reason'],'RRN_MATCH')
    def test_same_phone_different_name_holds(self):
        self.assertEqual(decide({'data2':['OTHER'],'data1':['P']},[person()])['action'],'hold')
    def test_same_address_does_not_merge(self):
        existing=person();existing['data4']=['A']
        # 주소만 같은 다른 이름은 기존 PID에 합치지 않고 새 PID로 만듭니다(v3).
        self.assertEqual(decide({'data2':['OTHER'],'data4':['A']},[existing])['action'],'create')
    def test_customer_and_agent_phones_are_separate(self):
        text='고객: 가람 010-0000-0001\n담당자: 나래 010-0000-0002'
        records=[mention(text,'가람','person','N1'),mention(text,'나래','person','N2'),mention(text,'010-0000-0001','phone_number','P1'),mention(text,'010-0000-0002','phone_number','P2')]
        subjects,unassigned=group_records(text,records)
        by_name={s['name_token']:s for s in subjects}
        self.assertEqual(by_name['N1']['values']['data1'],['P1'])
        self.assertEqual(by_name['N2']['values']['data1'],['P2'])
        self.assertEqual(unassigned,[])
    def test_ambiguous_owner_not_assigned(self):
        text='가람과 나래가 문의함.\n환급 계좌: 123-456'
        records=[mention(text,'가람','person','N1'),mention(text,'나래','person','N2'),mention(text,'123-456','account_number','A')]
        subjects,unassigned=group_records(text,records)
        self.assertEqual(len(unassigned),1)
        self.assertTrue(all(not s['values']['data6'] for s in subjects))
    def test_multiple_cards_are_kept(self):
        text='고객: 가람 / 카드번호 1111 / 카드번호 2222'
        records=[mention(text,'가람','person','N1'),mention(text,'1111','card_number','C1'),mention(text,'2222','card_number','C2')]
        subjects,_=group_records(text,records)
        self.assertEqual(subjects[0]['values']['data7'],['C1','C2'])
    def test_diff_phone_groups_in_one_doc_are_not_forced_together(self):
        text='고객: 가람 010-0000-0001\n고객: 가람 010-0000-0002'
        first=mention(text,'가람','person','N');second=dict(first,start=text.rindex('가람'),end=text.rindex('가람')+2)
        records=[first,second,mention(text,'010-0000-0001','phone_number','P1'),mention(text,'010-0000-0002','phone_number','P2')]
        subjects,_=group_records(text,records)
        self.assertEqual(subjects[0]['force_hold'],'LOCAL_IDENTITY_AMBIGUOUS')
    def test_missing_mapping_holds(self):
        subject={'owner_key':'N|고객','name_token':'N','role':'고객','values':clean_values({'data2':['N']}),'lines':[1],'evidence':[],
                 'force_hold':None,'checks':[{'token':'N','label':'person','normalized':'가람'}]}
        prepared=[{'doc_id':'D','source_hash':'hash','subjects':[subject],'unassigned':[],'document_hold':None}]
        events,_,people=process_prepared(prepared,[],{}, {})
        self.assertEqual(events[0]['reason'],'TOKEN_MAPPING_MISSING')
        self.assertEqual(people,[])
    def test_existing_state_makes_name_only_rerun_idempotent(self):
        from starter.person_dictionary import state_key
        subject={'owner_key':'N|고객','name_token':'N','role':'고객','values':clean_values({'data2':['N']}),'lines':[1],'evidence':[],
                 'force_hold':None,'checks':[{'token':'N','label':'person','normalized':'가람'}]}
        doc={'doc_id':'D','source_hash':'hash','subjects':[subject],'unassigned':[],'document_hold':None}
        state={state_key(doc,subject):{'pid':1,'doc_id':'D'}}
        events,_,people=process_prepared([doc],[person(phone=None)],{'N':{'label':'person','normalized':'가람'}},state)
        self.assertEqual(events[0]['action'],'unchanged')
        self.assertEqual(len(people),1)


class SensitivityTests(unittest.TestCase):
    def test_strict_phone_is_not_enough(self):
        self.assertEqual(decide({'data2':['N'],'data1':['P']},[person()], 'strict')['action'],'hold')
    def test_strict_rrn_is_enough(self):
        self.assertEqual(decide({'data2':['N'],'data5':['R']},[person(rrn='R')], 'strict')['action'],'merge')
    def test_relaxed_requires_two_independent_types(self):
        p=person();p.update(data3=['E','E2'],data6=['A'],data4=['D'])
        self.assertEqual(decide({'data2':['N'],'data3':['E','E2'],'data4':['D']},[p],'relaxed')['action'],'hold')
        v={'data2':['N'],'data3':['E'],'data6':['A']}
        self.assertEqual(decide(v,[p],'relaxed')['reason'],'SECONDARY_MATCH')
        self.assertEqual(decide(v,[p],'balanced')['action'],'hold')
    def test_all_profiles_block_name_only_and_conflicts(self):
        for mode in ('strict','balanced','relaxed'):
            self.assertEqual(decide({'data2':['N']},[person()],mode)['action'],'hold')
            # 주민번호가 일치하면 다른 전화번호는 추가 연락처로 연결합니다(v3). 주민번호 없이 전화만 다르면 보류합니다.
            self.assertEqual(decide({'data2':['N'],'data1':['NEW'],'data5':['R']},[person(rrn='R')],mode)['action'],'merge')
            self.assertEqual(decide({'data2':['N'],'data1':['NEW']},[person(rrn='R')],mode)['reason'],'PHONE_DIFFERENT')
            self.assertEqual(decide({'data2':['N'],'data1':['P'],'data5':['R2']},[person(rrn='R')],mode)['action'],'hold')
    def test_role_conflict(self):
        self.assertEqual(decide({'data2':['N'],'data1':['P']},[person()],role='직원/담당자',known_roles={1:{'고객/신청인'}})['reason'],'ROLE_CONFLICT')
    def test_invalid_setting_and_missing_name(self):
        with self.assertRaises(ValueError):decide({},[],'wrong')
        self.assertEqual(decide({'data1':['P']},[])['reason'],'NAME_REQUIRED')
    def test_profile_changes_state_key(self):
        from starter.person_dictionary import state_key
        d={'doc_id':'D','source_hash':'H'};s={'owner_key':'N|고객'}
        self.assertNotEqual(state_key(d,s,'strict'),state_key(d,s,'balanced'))
    def test_preview_does_not_mutate_database_and_rejects_stale_plan(self):
        from unittest.mock import patch,MagicMock
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from starter import person_dictionary as mod
        cur=MagicMock();cur.fetchall.return_value=[]
        conn=MagicMock();conn.cursor.return_value.__enter__.return_value=cur
        with TemporaryDirectory() as temp,patch.object(mod,'prepare_documents',return_value=[]),patch.object(mod,'current_people',return_value=[]),patch.object(mod.db,'vault_conn') as factory,patch.object(mod,'report_dir',return_value=Path(temp)):
            factory.return_value.__enter__.return_value=conn
            result=mod._build_existing(sensitivity='balanced')
            self.assertFalse(result['applied'])
            self.assertFalse(any('INSERT INTO' in str(c) or 'UPDATE vault.person' in str(c) for c in cur.execute.call_args_list))
            cur.fetchall.side_effect=[[(c,) for c in COLUMNS],[],[],[],[]]  # 열 확인, PID 잠금, token_map, 수동 판정, 이전 연결
            with self.assertRaisesRegex(ValueError,'미리보기'):
                mod._build_existing(apply=True,sensitivity='balanced',expected_plan='stale')


class AdminApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_and_same_session_apply(self):
        from unittest.mock import patch,AsyncMock
        from server_admin import app as web
        web._person_previews.clear()
        with patch.object(web,'mutation',return_value='session'),patch.object(web,'payload',new_callable=AsyncMock) as payload,patch.object(web,'_person_run',return_value={'plan':'digest','sensitivity':'strict'}) as run,patch.object(web,'audit'):
            payload.return_value={'sensitivity':'strict'}
            await web.person_preview(None)
            payload.return_value={'sensitivity':'strict','plan':'digest','confirmed':True}
            await web.person_apply(None)
            self.assertEqual(run.call_args.args,('strict',True,'digest'))
            self.assertNotIn('session',web._person_previews)
    async def test_apply_rejects_wrong_session_profile_plan_or_confirmation(self):
        import time
        from unittest.mock import patch,AsyncMock
        from fastapi import HTTPException
        from server_admin import app as web
        for payload_value,session in [({'sensitivity':'strict','plan':'digest','confirmed':True},'other'),({'sensitivity':'relaxed','plan':'digest','confirmed':True},'session'),({'sensitivity':'strict','plan':'old','confirmed':True},'session'),({'sensitivity':'strict','plan':'digest','confirmed':False},'session')]:
            web._person_previews.clear();web._person_previews['session']=('digest','strict',time.monotonic())
            with patch.object(web,'mutation',return_value=session),patch.object(web,'payload',new_callable=AsyncMock,return_value=payload_value),patch.object(web,'_person_run') as run:
                with self.assertRaises(HTTPException):await web.person_apply(None)
                run.assert_not_called()
    async def test_preview_requires_authentication_and_valid_profile(self):
        from unittest.mock import patch,AsyncMock
        from fastapi import HTTPException
        from server_admin import app as web
        with patch.object(web,'mutation',side_effect=HTTPException(401)),patch.object(web,'_person_run') as run:
            with self.assertRaises(HTTPException):await web.person_preview(None)
            run.assert_not_called()
        with patch.object(web,'mutation',return_value='session'),patch.object(web,'payload',new_callable=AsyncMock,return_value={'sensitivity':'unknown'}),patch.object(web,'_person_run') as run:
            with self.assertRaises(HTTPException):await web.person_preview(None)
            run.assert_not_called()


class MentionRowTests(unittest.TestCase):
    def test_subject_and_orphan_rows(self):
        from starter.person_dictionary import mention_rows
        prepared=[{'doc_id':'D','source_hash':'H'}]
        events=[{'doc_id':'D','action':'create','reason':'NO_CANDIDATE','pid':5,'owner_key':'N|고객','role':'고객','name_token':'N','values':{'data2':['N'],'data1':['P']}},
                {'doc_id':'D','action':'hold','reason':'NAME_ONLY','pid':None,'owner_key':'M|역할 미확인','role':'역할 미확인','name_token':'M','values':{'data2':['M']}},
                {'doc_id':'D','action':'hold_feature','reason':'OWNER_AMBIGUOUS','label':'account_number','token':'A','line':3},
                {'doc_id':'D','action':'hold_document','reason':'NO_PERSON_DETECTED'}]
        rows={r['owner_key']:r for r in mention_rows(prepared,events,'balanced')}
        self.assertEqual(rows['N|고객']['status'],'created');self.assertEqual(rows['N|고객']['pid'],5);self.assertEqual(rows['N|고객']['data1'],['P'])
        self.assertEqual(rows['M|역할 미확인']['status'],'held');self.assertIsNone(rows['M|역할 미확인']['pid'])
        orphan=rows['orphan|account_number|A|3']
        self.assertIsNone(orphan['name_token']);self.assertEqual(orphan['data6'],['A']);self.assertEqual(orphan['status'],'held')
        self.assertEqual(len(rows),3)
        self.assertTrue(all(r['source_hash']=='H' and r['sensitivity']=='balanced' for r in rows.values()))


class ManualReviewTests(unittest.TestCase):
    def subject(self,token='N',phone='P'):
        return {'owner_key':token+'|고객','name_token':token,'role':'고객','values':clean_values({'data2':[token],'data1':[phone]}),'lines':[1],'evidence':[],
                'force_hold':None,'checks':[{'token':token,'label':'person','normalized':'가람'},{'token':phone,'label':'phone_number','normalized':'0100'}]}
    def mapping(self):
        return {'N':{'label':'person','normalized':'가람'},'P':{'label':'phone_number','normalized':'0100'}}
    def test_manual_decision_skips_auto(self):
        doc={'doc_id':'D','source_hash':'h','subjects':[self.subject()],'unassigned':[],'document_hold':None}
        events,_,people=process_prepared([doc],[person()],self.mapping(),{},manual={('D','N|고객'):('manual_created',9,'고객')})
        self.assertEqual(events[0]['action'],'manual');self.assertEqual(events[0]['pid'],9)
        self.assertEqual(people,[person()])
    def test_shared_token_is_not_evidence_or_saved(self):
        doc={'doc_id':'D','source_hash':'h','subjects':[self.subject()],'unassigned':[],'document_hold':None}
        events,_,people=process_prepared([doc],[person()],self.mapping(),{},shared={'P'})
        self.assertEqual(events[0]['reason'],'NAME_ONLY')
        self.assertEqual(people,[person()])
    def test_manual_orphan_feature_is_not_held_again(self):
        doc={'doc_id':'D','source_hash':'h','subjects':[],'unassigned':[{'label':'account_number','token':'A','line':2,'reason':'OWNER_AMBIGUOUS'}],'document_hold':'NO_PERSON_DETECTED'}
        events,_,_=process_prepared([doc],[],{},{},manual={('D','orphan|account_number|A|2'):('shared',None,None)})
        self.assertEqual([e['action'] for e in events],['hold_document','manual'])


class PriorLinkTests(unittest.TestCase):
    """정책 버전이 바뀐 재실행에서 PID를 만든 문서가 자기 PID와 이름만 겹쳐 보류되던 문제."""
    subject=ManualReviewTests.subject; mapping=ManualReviewTests.mapping
    def name_only_doc(self,source_hash='h'):
        subject=self.subject(); subject['values']=clean_values({'data2':['N']}); subject['checks']=subject['checks'][:1]
        return {'doc_id':'D','source_hash':source_hash,'subjects':[subject],'unassigned':[],'document_hold':None}
    def test_prior_link_is_kept_on_policy_change(self):
        events,_,people=process_prepared([self.name_only_doc()],[person()],self.mapping(),{},prior={('D','N|고객'):(1,'created','NO_CANDIDATE','h','고객')})
        self.assertEqual(events[0]['action'],'unchanged');self.assertEqual(events[0]['pid'],1);self.assertEqual(events[0]['keep_status'],'created')
        self.assertEqual(people,[person()])
        from starter.person_dictionary import mention_rows
        row=mention_rows([{'doc_id':'D','source_hash':'h'}],events,'balanced')[0]
        self.assertEqual((row['status'],row['pid'],row['reason']),('created',1,'NO_CANDIDATE'))
    def test_changed_document_is_decided_again(self):
        events,_,_=process_prepared([self.name_only_doc('changed')],[person()],self.mapping(),{},prior={('D','N|고객'):(1,'created','NO_CANDIDATE','h','고객')})
        self.assertEqual(events[0]['reason'],'NAME_ONLY')
    def test_prior_pid_without_the_values_is_decided_again(self):
        other={**person(),'pid':2,'data2':['X']}
        events,_,_=process_prepared([self.name_only_doc()],[person(),other],self.mapping(),{},prior={('D','N|고객'):(2,'linked','NAME_PHONE_MATCH','h','고객')})
        self.assertNotEqual(events[0]['action'],'unchanged')


class RulesV3Tests(unittest.TestCase):
    def test_two_rrns_are_not_saved_or_used(self):
        r=decide({'data2':['N'],'data5':['R1','R2'],'data1':['P']},[person()])
        self.assertEqual(r['action'],'merge');self.assertEqual(r['exclude']['data5'],['R1','R2']);self.assertTrue(r['rrn_ambiguous'])
    def test_homonym_with_different_rrn_gets_new_pid(self):
        r=decide({'data2':['N'],'data5':['R2']},[person(phone=None,rrn='R1')])
        self.assertEqual((r['action'],r['reason']),('create','HOMONYM_RRN_DIFFERENT'))
    def test_homonym_rule_needs_rrn_on_every_candidate(self):
        r=decide({'data2':['N'],'data5':['R2']},[person(phone=None,rrn='R1'),person(2,phone=None)])
        self.assertEqual(r['action'],'hold')
    def test_rrn_match_accepts_additional_phone(self):
        r=decide({'data2':['N'],'data1':['P2'],'data5':['R']},[person(rrn='R')])
        self.assertEqual((r['action'],r['reason']),('merge','RRN_MATCH'))
    def test_strict_ignores_phone_multiplicity(self):
        r=decide({'data2':['N'],'data1':['P'],'data5':['R1']},[person(1,rrn='R1'),person(2,rrn='R2')],'strict')
        self.assertEqual((r['action'],r['pid']),('merge',1))
    def test_values_of_other_pid_are_not_absorbed(self):
        other=person(2,name='M',phone=None);other['data7']=['C2']
        r=decide({'data2':['N'],'data1':['P'],'data7':['C2']},[person(),other])
        self.assertEqual(r['exclude'],{'data7':['C2']})
        doc={'doc_id':'D','source_hash':'h','subjects':[{'owner_key':'N|고객','name_token':'N','role':'고객','values':clean_values({'data2':['N'],'data1':['P'],'data7':['C2']}),'lines':[1],'evidence':[],'force_hold':None,'checks':[]}],'unassigned':[],'document_hold':None}
        _,_,people=process_prepared([doc],[person(),other],{},{})
        self.assertEqual(people[0]['data7'],[])
    def test_new_name_sharing_only_address_is_created(self):
        p=person();p['data4']=['A']
        self.assertEqual(decide({'data2':['NEW'],'data4':['A']},[p])['reason'],'NEW_NAME_SHARED_ADDRESS')
    def test_new_name_sharing_phone_still_holds(self):
        self.assertEqual(decide({'data2':['NEW'],'data1':['P']},[person()])['reason'],'OTHER_SHARED_VALUE')


class DocumentRoleFallbackTests(unittest.TestCase):
    def test_form_field_goes_to_single_customer(self):
        text='신청인: 가람\n담당: 나래 과장\n\n주민등록번호: 900101-1234567\n대표번호: 1588-0000'
        records=[mention(text,'가람','person','N1'),mention(text,'나래','person','N2'),mention(text,'900101-1234567','resident_registration_number','R'),mention(text,'1588-0000','phone_number','P')]
        subjects,unassigned=group_records(text,records)
        by={s['name_token']:s for s in subjects}
        self.assertEqual(by['N1']['values']['data5'],['R'])
        self.assertEqual([u['token'] for u in unassigned],['P'])
    def test_two_customers_keep_field_unassigned(self):
        text='고객: 가람\n고객: 나래\n\n연락처: 010-1111-2222'
        records=[mention(text,'가람','person','N1'),mention(text,'나래','person','N2'),mention(text,'010-1111-2222','phone_number','P')]
        _,unassigned=group_records(text,records)
        self.assertEqual([u['token'] for u in unassigned],['P'])


if __name__=='__main__':unittest.main()
