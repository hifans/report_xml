# -*- coding: utf-8 -*-

import subprocess
import os
import report
import tempfile
import time
from mako.template import Template
from mako import exceptions
import netsvc
import pooler

import report_webkit

from report.report_sxw import *
import addons
import tools
from tools.translate import _
from osv.osv import except_osv


from lxml import etree as ET
from lxml.builder import E

## XXXvlab: Translation ?


class Obj2Xml():

    _attr_keep_fields = ["domain", "relation", "type", "help", "name", "string"]

    _dump_dispatcher = [(dict, "_xml_dict"),
                        ((list, tuple), "_xml_list"),
                        (unicode, "_xml_unicode"),
                        (bool, "_xml_bool"),
                        ((int, basestring, float), "_xml_str")
                        ]

    def __init__(self, **kwargs):
        self.meta = {}
        for k, v in kwargs.iteritems():
            self.meta[k] = v

        self.uid = kwargs['uid']
        self.context = kwargs['context']
        self.cr = kwargs['cr']

    def context2xml(self):
        context = self.obj2xml(self.context)
        context.tag = "context"
        return context

    def meta2xml(self):
        meta = self.obj2xml(self.meta)
        meta.tag = "meta"
        return meta

    def report(self, objs, deep):

        ## Structure:
        ## dump @model
        ##    meta
        ##       context
        ##    element @model @id
        ##       attr @oe-type (@relation)

        context = self.context2xml()
        #meta = self.meta2xml()
        xmlobjs = [self.obj2xml(obj, deep=deep) for obj in objs]
        return E.report(
            #meta,
            context,
            E.data(*xmlobjs),
        )

    def get_fields_def(self, obj):
        t = obj._table
        return t.fields_get(self.cr, self.uid, None, self.context)

    def _xml_dict(self, obj, deep, cache):
        elts = []
        for k, v in obj.iteritems():
            try:
                xml = self.obj2xml(v, deep=deep, cache=cache)
            except NotImplementedError:
                continue ## ignore bad field.
            elts.append(getattr(E, k)(xml))
        if len(elts) == 0:
            return ""
        return E.dict(*elts)

    def _xml_list(self, obj, deep, cache):
        elts = []
        for v in obj:
            try:
                xml = self.obj2xml(v, deep=deep, cache=cache)
            except NotImplementedError:
                continue ## ignore bad field.
            elts.append(E.li(xml))
        if len(elts) == 0:
            return ""
        return E.ul(*elts)

    def _xml_str(self, obj, deep, cache):
        return str(obj)

    def _xml_unicode(self, obj, deep, cache):
        return unicode(obj)

    def _xml_bool(self, obj, deep, cache):
        return E.bool(value=str(obj))

    def _xml_oe_object(self, obj, deep, cache):

        F = getattr(E, "oe-object")
        attrs = {
            "type": obj.__class__.__name__,
        }

        if not hasattr(obj, '_table') or obj._table is None:
            class_name = obj.__class__.__name__
            if class_name == 'browse_record_list':
                return F(*(self.obj2xml(o, deep=deep, cache=cache)
                           for i,o in enumerate(obj)))
            if class_name == 'browse_null':
                return "" ## element is removed

            raise NotImplementedError("This oe-object is unknown: %r (type: %r)" % (obj, type(obj)))

        attrs.update({
            "table": obj._table_name,
            "id": str(obj._id),
            })

        # Using repr as id...
        cached_value = cache.get(str(obj), None)
        if cached_value:
            return F(cropped="ALREADY_DEFINED", **attrs)

        if deep == 0:
            return F(cropped="MAX_DEEPNESS_REACHED", **attrs)

        res = cache[str(obj)] = F(**attrs)
        for key, field_def in self.get_fields_def(obj).iteritems():
            value = getattr(obj, key)
            value = self.obj2xml(value, deep=deep - 1, cache=cache)
            G = getattr(E, key)

            attr = dict((k, unicode(v))
                        for k, v in field_def.iteritems()
                        if k in self._attr_keep_fields) ## XXXvlab: what should I do of the states ?
	    #attr["name"] = key

            res.append(G(value, **attr))
        ## XXXvlab: obj or the type itself ?
        ## XXXvlab: what attribute ?

        return res

    def obj2xml(self, obj, deep=3, cache=None):

        cache = cache or {}

        for types, fn_name in self._dump_dispatcher:
            if isinstance(obj, types):
                return getattr(self, fn_name, types)(obj, deep, cache)

        ## XXXvlab: yuk ! I would have appreciated to have a common ancestor to
        ## osv object...

        module_name = obj.__class__.__module__
        if (module_name.startswith('osv')               ## openerp 6.0.3
            or module_name.startswith('openerp.osv')):  ## openerp 6.1
            return self._xml_oe_object(obj, deep, cache)

        raise NotImplementedError("Dump not implemented for %r (type: %r)" % (obj, type(obj)))


class XmlParser(report_webkit.webkit_report.WebKitParser):
    """Custom class that dump data to XML reports
       Code partially taken from report webkit. Thanks guys :)
    """

    def __init__(self, name, table, rml=False, parser=False,
        header=True, store=False):
        self.parser_instance = False
        self.localcontext={}
        super(XmlParser, self).__init__(name, table, rml,
                                        parser, header, store)

    def generate_pdf(self, comm_path, report_xml, header, footer, html_list):
        ## should return the raw data of a pdf
        return None

    # override needed to keep the attachments' storing procedure
    def create_single_pdf(self, cr, uid, ids, data, report_xml, context=None):
        """generate the PDF"""

        if context is None:
            context={}

        if report_xml.report_type != 'xml':
            return super(XmlParser,self).create_single_pdf(cr, uid, ids, data, report_xml, context=context)

        model = self.table
        pool = pooler.get_pool(cr.dbname)
        table_obj = pooler.get_pool(cr.dbname).get(model)
        objs = table_obj.browse(cr, uid, ids, list_class=None, context=context, fields_process=None)
        toXml = Obj2Xml(cr=cr, uid=uid, context=context)

        xml_output = ET.tostring(toXml.report(objs, deep=6),
                                 pretty_print=True,
                                 xml_declaration=True,
                                 encoding="utf-8")

        return (xml_output, 'xml')


    def create(self, cursor, uid, ids, data, context=None):
        """We override the create function in order to handle generator
           Code taken from report webkit. Thanks guys :) """

        pool = pooler.get_pool(cursor.dbname)
        ir_obj = pool.get('ir.actions.report.xml')
        report_xml_ids = ir_obj.search(cursor, uid,
                [('report_name', '=', self.name[7:])], context=context)
        if report_xml_ids:
            report_xml = ir_obj.browse(
                                        cursor,
                                        uid,
                                        report_xml_ids[0],
                                        context=context
                                    )
            report_xml.report_rml = None
            report_xml.report_rml_content = None
            report_xml.report_sxw_content_data = None
            report_rml.report_sxw_content = None
            report_rml.report_sxw = None
        else:
            return super(XmlParser, self).create(cursor, uid, ids, data, context)
        if report_xml.report_type != 'xml' :
            return super(XmlParser, self).create(cursor, uid, ids, data, context)
        result = self.create_source_pdf(cursor, uid, ids, data, report_xml, context)
        if not result:
            return (False,False)
        return result
